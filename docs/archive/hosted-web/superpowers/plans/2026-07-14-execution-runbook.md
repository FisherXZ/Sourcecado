# Execution Runbook — Runtime Solidification Sprint (ralph loop)

Date: 2026-07-14 · Status: DRAFT for Fisher's skim
Spec: `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md`
Plans: `docs/superpowers/plans/2026-07-14-r{N}-*-plan.md` (one per slice)
Contracts: `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`

## Execution order

Strict sequence, one slice at a time; a slice starts only when the previous
one's gate is green and merged:

```
R0 → R1 → R2 → R3 → R4 → R5 → R7 → R6 → R8 → R9
```

(R3 could start after R2 in parallel, but ralph runs solo — serial keeps the
gates meaningful. R7 before R6: external tools prove the orchestrator before
the sessions slice touches the stream route. R8 last-but-one so dep removal
can't strand a mid-sprint revert. Cut order if over budget: Apollo half of
R7 → R8 → R6.)

## Session isolation (one slice = one fresh Claude Code session)

Each R slice runs in its OWN session so no prior slice's context pollutes the
window. All persistence is on disk (planning-with-files), never in the chat:

- Per slice: fresh session (`/clear` or new `claude`), then a `/ralph-loop`
  prompt scoped to THAT slice only, with `--completion-promise "R{N}_DONE"`
  and `--max-iterations 25`. The prompt must forbid starting the next slice.
- First act of every session: reconcile with reality — read `task_plan.md`,
  `progress.md`, the slice plan's checkboxes, and `git status`/`git log`.
  Partial work from an interrupted session is normal; continue, don't redo.
- After each task: tick the plan file's checkboxes and append one line to
  `progress.md`. A session must be killable at any moment with zero lost
  state.
- Slice done (gates green, PR open) → output the promise → session ENDS.
  Fisher merges. Next slice starts in a NEW session.

## Per-slice protocol

1. Branch off up-to-date `main`: `feat/rt-r{N}-<slug>`.
2. Execute the slice's plan file top to bottom. The plan is the contract —
   no scope beyond it. If the plan is wrong, STOP and flag; don't improvise.
3. Gate (all must pass before PR):
   - `npm run build` — green (zero webpack/type errors)
   - `npx tsc --noEmit` — clean
   - `npm test` — meets baseline below (no NEW failures)
   - From R2 onward: one live chat probe — `npm run dev`, ask one memory
     question in `/chat`, confirm answer + run appears in `/runs/[id]`
4. PR to `main`, slice-scoped title `feat(rt-R{N}): ...`; merge when green.
5. Log outcome (files, test delta, surprises) in `progress.md`.

## Baseline (recorded 2026-07-14, after build fix)

- Build: GREEN (fixed today — `.js` import specifiers stripped from 9
  web-reachable files + 2 type errors the build had never reached).
- `npm test`: **255 passed / 78 failed / 1 todo (334)**.
  The 78 failures are ALL the legacy SQLite CLI suites (`tests/answer`,
  `cli`, `db`, `ingest*`, `stress` — better-sqlite3 bindings vs current
  Node; `npm rebuild` fails on gyp). Pre-existing P0 in TODOS.md, out of
  sprint scope. **Gate = 255+ passing, zero new failures.** Any slice that
  drops a currently-passing test is broken, full stop.
- OPEN DECISION (Fisher): exclude the 78 sqlite suites from `npm test` via
  vitest config so the gate is a clean "all green", or leave them visible
  as a reminder? (Excluding touches vitest.config.ts once, in R0.)

## Environment prerequisites (state as of 2026-07-14 14:30)

- Postgres: `docker compose up -d` (Docker Desktop must be running —
  container `sourcecado-db-1`). Migrations: `npm run migrate`.
- Env: everything lives in `.env.local`. Next.js loads it automatically;
  **scripts and vitest do NOT** — run as
  `set -a; source .env.local; set +a; npm test`. Ralph must do this.
- Keys present: DATABASE_URL, OPENAI (fresh 2026-07-14), TAVILY (fresh
  2026-07-14), DEEPSEEK (verified live 2026-07-15).
  ANTHROPIC verified live 2026-07-15 (200 + real completion — R9 FULLY
  unblocked). Missing: APOLLO_API_KEY (Fisher adds later; R7 Apollo work
  is mock-tested until then).
- Cleanup note: `QWEN_API_KEY` still sits in `.env.local` but nothing reads
  it (dropped from .env.example in afb1cd6) — safe to delete manually.

## Stop conditions (ralph must halt and ask)

- A gate fails twice on the same slice after honest fix attempts.
- The plan conflicts with the contracts brief or the spec.
- Any change to `src/lib/memory/{retrieve,embed,chunk,permissions}.ts`
  beyond what a plan names explicitly (pgvector engine is untouchable).
- Anything requiring a new dependency not named in a plan.
- A secret would need to be committed or echoed. Hard stop (P0 rule).

## Human gates (Fisher)

- Before R1 starts: skim contracts brief + R1/R2 plans (~20 min).
- After R5: 5-minute live look at streaming chat (motion/typed events).
- After R9: final provider-swap demo sign-off.
