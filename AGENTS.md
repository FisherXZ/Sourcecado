# Sourcecado agent context

## Product direction

Sourcecado is a local-first desktop assistant for a Codeology sourcing director. The current spring proves the complete job for one operator on one machine: name a target, find people, prepare tailored outreach, deliberately enrich when needed, approve and send, keep conversations moving, and leave a person file another officer can pick up.

Chat is home. The board is the assistant's operating picture. The person file is the durable domain object.

The active runtime is `desktop/`. The previous hosted Next.js/Postgres application is preserved in `archive/hosted-web/` and must not be treated as the current implementation.

## Current guardrails

- Build the local Sourcecado desktop product: Python/FastAPI sidecar plus React/Vite/Tauri UI.
- Optimize for one sourcing director now while keeping person files intelligible to a later officer.
- A sequence is a person being worked through Open, In conversation, and Done. A company is context, not a deal.
- Apollo search may return candidates without an email. Enrichment is manual and credit-aware.
- Sending is allowed only after explicit review and approval in Sourcecado. Never add auto-send.
- Keep Gmail, Drive, Calendar, Granola, Apollo, and web work attached to the relevant person and run ledger.
- Store secrets and runtime state outside the repository. Never log tokens, API keys, or raw authorization headers.
- Preserve tool calls, artifacts, source references, permission decisions, rationale summaries, and failures in the local ledger.
- Do not reintroduce the hosted app, Postgres, Next.js, or team tenancy without a new product decision.
- Do not make OpenClaw, Hermes, or MCP the core product dependency. Borrow useful patterns behind Sourcecado-owned boundaries.

## Repository map

- `desktop/coworker/` — sidecar, connectors, agent loop, policy, and persistence
- `desktop/surfaces/gui/` — active React/Vite/Tauri UI
- `desktop/tests/` and `desktop/surfaces/gui/tests/` — active verification suites
- `docs/` — current and historical product/engineering records
- `archive/hosted-web/` — read-only historical implementation

## Documentation precedence

1. `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md` — current product source of truth
2. `README.md`, this file, `CONTEXT.md`, and `DESIGN.md` — current operating guidance
3. Current dated implementation plans and ADRs listed in `docs/README.md`
4. Older dated specs and plans — historical context only

If documents conflict, follow the highest item in this list and update the stale living document in the same change.
