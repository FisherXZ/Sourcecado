# Sourcecado agent context

## Product direction

Sourcecado is a local-first desktop assistant for a Codeology sourcing director. The current spring proves the complete job for one operator on one machine: name a target, find people, prepare tailored outreach, deliberately enrich when needed, approve and send, keep conversations moving, and leave a person file another officer can pick up.

Chat is home. Contacts is the assistant's operating picture for saved people and their sequences. The person file is the durable domain object.

The active runtime is `backend/` (Python) and `frontend/` (React and Tauri). The previous hosted Next.js/Postgres application is preserved in `archive/hosted-web/` and must not be treated as the current implementation.

## Current guardrails

- Build the local Sourcecado desktop product: Python/FastAPI backend plus React/Vite/Tauri UI.
- Optimize for one sourcing director now while keeping person files intelligible to a later officer.
- A sequence is a person being worked through Open, In conversation, and Done. A company is context, not a deal.
- A kept person remains durable before outreach. The Contacts people table shows every live saved person: Open, In conversation, Done, or Not started (kept without a sequence). Its Tasks view includes the same people. Saving a task leaves sequence membership unchanged.
- Apollo search may return candidates without an email. Enrichment is manual and credit-aware.
- Sending is allowed only after explicit review and approval in Sourcecado. Never add auto-send.
- Keep Gmail, Drive, Calendar, Granola, Apollo, and web work attached to the relevant person and run ledger.
- Store secrets and runtime state outside the repository. Never log tokens, API keys, or raw authorization headers.
- Preserve tool calls, artifacts, source references, permission decisions, rationale summaries, and failures in the local ledger.
- Sourcecado is moving to a hosted, shared club workspace (Vercel, Railway, Supabase Postgres) under [ADR 0007](docs/engineering/adr/0007-hosted-club-workspace.md). Do that work only through the numbered slices in [the slice plan](docs/superpowers/plans/2026-11-09-hosted-migration-slices.md). Until a store's slice merges, its local implementation is still the live one.
- Every hosted query is scoped by `workspace_id`, text columns are `COLLATE "C"` in migrations, and every table has RLS. Do not reintroduce Next.js or `archive/hosted-web/`.
- Do not make OpenClaw, Hermes, or MCP the core product dependency. Borrow useful patterns behind Sourcecado-owned boundaries.

## Repository map

- `backend/coworker/` — backend, connectors, agent loop, policy, and persistence
- `frontend/` — active React/Vite/Tauri UI
- `backend/tests/` and `frontend/tests/` — active verification suites
- `docs/` — current and historical product/engineering records
- `docs/course/` — Sourcecado Course language and plan. Product language in `docs/domain.md` takes precedence over course terminology.
- `archive/hosted-web/` — read-only historical implementation

## Documentation precedence

1. `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md` — current product source of truth
   - For runtime, hosting, tenancy and persistence, `docs/superpowers/specs/2026-11-09-hosted-migration.md` and ADR 0007 take precedence over older local-first guidance.
2. `README.md`, this file, `docs/domain.md`, `docs/design.md`, and `CONTRIBUTING.md` — current operating guidance
3. Current dated implementation plans and ADRs listed in `docs/README.md`
4. Older dated specs and plans — historical context only

If documents conflict, follow the highest item in this list and update the stale living document in the same change.

## Read for the task

- For setup, testing or a first contribution, read [CONTRIBUTING.md](CONTRIBUTING.md).
- For product concepts, read [docs/domain.md](docs/domain.md).
- Before visual changes, read [docs/design.md](docs/design.md). Preserve the Warm Operator direction unless the user approves a change.
- Before changing approvals, persistence or recovery, read the relevant [engineering guide](docs/engineering/README.md).
- For active work and deliberate deferrals, read [docs/BACKLOG.md](docs/BACKLOG.md).
