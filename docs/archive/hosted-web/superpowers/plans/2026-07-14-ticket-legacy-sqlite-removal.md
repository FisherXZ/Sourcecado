# Ticket: Remove the legacy SQLite CLI stack (post-sprint)

Date: 2026-07-14 · Status: TICKETED — do NOT execute during the runtime
solidification sprint (R0–R9). Blocked by: R9 merged.
Decision: Fisher chose deletion over vitest-exclusion or binding repair
(the 78 failing tests cover code the Postgres stack replaced).

## Context

78 tests fail in every `npm test` run: `tests/answer.test.ts`, `cli.test.ts`,
`db.test.ts`, `db-client.test.ts` (partial), `ingest*.test.ts`, `tests/stress/`.
All import `better-sqlite3`, whose native binding was built for an older Node
(NODE_MODULE_VERSION 131 vs 147 required); `npm rebuild` fails in node-gyp.
Tracked as P0 in TODOS.md since 2026-06-17. They test the pre-Postgres
SourcyAvo CLI, superseded by `src/lib/memory/*` + pgvector (PR #8) — dead
weight, ~11s per test run, and a permanent asterisk in every gate report.

## Scope — delete

- Legacy CLI-only modules (verify with the import check below before rm):
  `src/cli.ts`, `src/answer.ts`, `src/read-service.ts`, `src/refresh.ts`,
  `src/procedures.ts`, `src/db.ts` (root SQLite one, NOT `src/lib/db.ts`),
  `src/ingest.ts` (root, NOT `src/lib/memory/ingest.ts`), `src/embeddings.ts`
- Their scripts: whatever of `scripts/{ingest,refresh,stress}.ts` only serve
  the SQLite stack (scripts calling `src/lib/*` stay) + the matching
  package.json script entries
- The 78 failing test files listed above
- `better-sqlite3` from package.json dependencies
- TODOS.md P0 entry (resolved by deletion)

## Scope — KEEP (shared with the live web stack)

`src/frontmatter.ts`, `src/ingest-error.ts`, `src/csv.ts`, `src/chunk.ts`,
`src/types.ts`, `src/extractors/*` are imported by web-reachable
`src/lib/memory/{ingest,chunk,extract}.ts`. Keep in place, or (nicer) move
into `src/lib/` in a separate mechanical follow-up. Do not delete.

Corrections found during execution (2026-08-03):
- `src/chunk.ts` survives, but NOT because `src/lib/memory/chunk.ts` imports it
  — that file deliberately re-implements `slugifySourceId` to avoid pulling in
  better-sqlite3, and says so in a comment. The real chain is
  `src/chunk.ts` ← `src/extractors/llm.ts` ← `src/lib/memory/extract.ts`.
- `src/extractors/mock.ts` is on neither list. It is not web-reachable, but
  `tests/{extractors,memory-extract}.test.ts` use it as a test double. Keep.
- `scripts/{ingest,refresh}.ts` already import `src/lib/*`, not the SQLite
  stack. Only `scripts/stress.ts` (→ `tests/stress/harness.ts`) is deletable.
- `tests/fixtures/seed-data/` stays — `tests/extractors.test.ts` reads it.
  Only `tests/fixtures/stress/` goes.
- Two passing suites depended on delete-list code: `tests/procedures.test.ts`
  (deleted with `loadProcedures`, which has no caller outside the CLI) and
  `tests/slugify.test.ts` (repointed at `src/lib/memory/chunk.js` — the two
  `slugifySourceId` bodies are character-identical, and the live copy had no
  direct test).
- `procedures/*.md` kept. The J1 plan
  (`2026-07-15-sourcing-agent-system-prompt.md:12`) has an open checkbox
  pointing at `procedures/outreach-tone.md`; deleting it would invalidate an
  unrun ticket. The loader goes, the content stays.

## Preconditions

- R9 merged (sprint done) — R8 touches `src/extractors/llm.ts` and this
  ticket must not collide with slice file ownership.
- Run the import check to finalize the delete list; trust it over the lists
  above if they disagree:
  `grep -rn "from \"\.\./\.\./\|from \"\./" src/lib src/app | grep -oE '"[^"]+"' | sort -u`
  (any root `src/*.ts` file NOT reachable from `src/lib`/`src/app` and only
  reachable from `src/cli.ts`/`scripts/*` is deletable).

## Acceptance criteria

1. `npm test` — 0 failed (the 78 disappear with their files; no survivors
   skipped or excluded).
   Actual on 2026-08-03: **66 files, 436 passed / 0 failed / 2 skipped (438)**,
   down from 76 files, 78 failed / 440 passed / 2 skipped / 1 todo (521).
   The passing count drops by 5, not 0: the deleted files held 84 tests, of
   which 78 failed, 1 was a todo, and **5 passed** (2 in `procedures.test.ts`,
   3 inside otherwise-failing files). `+1` for the new `better-sqlite3`-absent
   assertion in `tests/package-deps.test.ts` gives 440 − 5 + 1 = 436.
2. `npm run build` green; `npx tsc --noEmit` clean.
3. `better-sqlite3` absent from package.json + lockfile.
4. No `src/lib/**` or `src/app/**` import resolves to a deleted file.
5. README/TODOS updated (CLI usage docs removed or marked historical).

## Effort

~1 focused session (mostly deletion + one careful import-graph pass).
