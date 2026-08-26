# Hosted web application archive

Status: retired on 2026-08-26 when the local Sourcecado desktop stack became the repository default.

This directory preserves the former Next.js 15 application, Postgres/pgvector memory layer, migrations, tests, public assets, and its original CI workflow. Files were moved together with Git history; the implementation was not deleted or flattened into documentation.

Use this code for historical reference and selective pattern recovery. It is not covered by current CI, product requirements, or security maintenance.

## Historical layout

- `src/` — Next.js app and hosted runtime
- `tests/` — Vitest suite for the hosted runtime
- `scripts/` — migration and ingestion commands
- `.github/workflows/ci.yml` — the workflow used by this implementation
- `artifacts/` — historical project recap and build-sequence pages

## Running the snapshot

If a task explicitly needs the old app:

```bash
cd archive/hosted-web
npm ci
docker compose up -d
export DATABASE_URL=postgresql://sourcecado:sourcecado@localhost:5432/sourcecado
npm run migrate
npm run dev
```

Treat these instructions as best-effort historical recovery. The active app's setup is documented in the repository root.
