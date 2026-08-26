# Sourcecado agent context

Read [AGENTS.md](AGENTS.md) before changing the repository. It is the canonical agent guidance.

The short version:

- The active product is the local Python/FastAPI plus React/Vite/Tauri stack under `desktop/`.
- The hosted Next.js/Postgres stack under `archive/hosted-web/` is historical and excluded from active CI.
- Chat is home, the board is the operating picture, and the person file is the durable domain object.
- Apollo enrichment and Gmail sending require explicit human action; never add auto-enrich or auto-send.
- Keep secrets, OAuth grants, tokens, and runtime databases outside the repository.
- The current product source of truth is `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md`.
- `docs/README.md` explains which dated documents are current and which are historical.

## Design system

Read `DESIGN.md` before visual or UI work. Preserve the Warm Operator direction unless the user approves a change.

## Skill routing

When the request matches an available skill, invoke it before acting.

- Product shaping → `/office-hours`
- Architecture and implementation plans → `/plan-eng-review`
- Bugs and regressions → `/investigate`
- Browser QA → `/qa` or `/qa-only`
- Diff review → `/review`
- Visual review → `/design-review`
- Shipping and pull requests → `/ship`
