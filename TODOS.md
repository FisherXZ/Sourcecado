# TODOS

## Completed

### Fix better-sqlite3 native bindings
**Priority:** P0 · **Added:** 2026-06-17 · **Resolved:** 2026-08-03 (R10)

Resolved by deletion rather than repair. The 78 failing tests covered the
pre-Postgres SourcyAvo CLI, which `src/lib/memory/*` + pgvector superseded.
R10 removed the CLI modules, their tests, and the `better-sqlite3` dependency;
`npm install` no longer needs `--ignore-scripts`.
