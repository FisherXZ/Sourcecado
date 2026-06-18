# TODOS

## CLI / Database

### Fix better-sqlite3 native bindings
**Priority:** P0
**Added:** 2026-06-17 (noticed on feat/f1-app-shell)

The CLI test suite (78 tests across `tests/db.test.ts`, `tests/answer.test.ts`, `tests/cli.test.ts`, `tests/ingest.test.ts`, `tests/ingest.source-id.test.ts`, `tests/stress/`) is entirely broken because `better-sqlite3` native bindings were never compiled. Installed with `npm install --ignore-scripts` to avoid a Node 24 / gyp failure. The web app test suite (`tests/health.test.ts`) is unaffected and passes.

Fix: either rebuild with the right Node version, switch to a pre-built binary, or replace `better-sqlite3` with a pure-JS SQLite driver. Given F2 is adding Postgres anyway, the CLI may be deprecated before this needs fixing.

## Completed
