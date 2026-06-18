# Sourcecado

Sourcecado is a hosted team sourcing operating system for Codeology. It preserves contacts, sourcing history, source citations, knowledge gaps, outcomes, and human feedback — and surfaces an autonomous sourcing agent that tells Sourcing Directors what to do next.

## Getting started

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) to see the app. Navigate to `/chat` for the Research Chat interface.

## Health check

```
GET /api/health → { "status": "ok" }
```

Ping this endpoint to confirm the app is up.

## Run tests

```bash
npm test
```

## Project structure

- `src/app/` — Next.js 15 app router pages and layouts
- `src/app/api/health/` — health endpoint
- `src/app/chat/` — Research Chat page
- `tests/` — Vitest test suite
- `docs/` — design specs, ADRs, and roadmap documents

## Documentation

- [AGENTS.md](AGENTS.md) — product direction, roadmap guardrails, and agent architecture
- [CHANGELOG.md](CHANGELOG.md) — version history
- [CONTEXT.md](CONTEXT.md) — domain language and sourcing terminology
- [TODOS.md](TODOS.md) — open work items and known issues
- [docs/superpowers/specs/](docs/superpowers/specs/) — full design specs

## Legacy CLI

The original `sourcyavo` CLI (ingest / refresh / ask) is not available in v0.2.0.0. The `better-sqlite3` native bindings require a rebuild; see TODOS.md. The web app is the primary interface going forward.
