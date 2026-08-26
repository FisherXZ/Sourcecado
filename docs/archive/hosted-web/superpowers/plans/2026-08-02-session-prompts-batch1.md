# Session prompts — batch 1 (Phase 0, freeze gate)

One prompt per session. Paste into a fresh session. Each one ends the same way:
setup, orient, **stop**, then `/interview-me` to settle scope before any plan.

**Order:** R10 → R11 → H1 → H2 → T1.

**Before any session: merge PR #20 (R8).** It is the only open PR — #21 was
discarded 2026-08-02 — so there is no migration collision. It touches
`package.json`, so every branch below should base off a `main` that contains it.

**Repo root** below means `/Users/fisher/Documents/GitHub2026/Sourcecado`.

## Reference systems — include the matching line in every prompt

Two reference implementations are on disk. A ticket with a match reads it before
proposing an approach; port the design, not the syntax (OpenWorker is Python).

```
CC = /Users/fisher/Documents/Good-Examples/claude-code-main
OW = /Users/fisher/Documents/GitHub2026/openworker
```

| Need | Reference | Ticket |
|---|---|---|
| Per-run budget / cost stop | `CC/src/query/tokenBudget.ts` | R11 |
| Approval gate | `OW/coworker/tools/ask.py` | J3 |
| Scoped standing grants | `OW/coworker/automation/models.py` → `grant_entries()` | J3 |
| Todo / plan tools | `OW/coworker/tools/todo.py`, `plan.py` | J2 |
| Subagent | `OW/coworker/tools/subagent.py` | J4 |
| Scheduler / routines | `OW/coworker/automation/scheduler.py`, `models.py` | E1 |
| Agent persona layer | `OW/coworker/agents/base.py` | J1 |
| Tool registry + risk levels | `OW/coworker/tools/registry.py` | J2, J3 |
| Course structure, leaderboard grading | `huggingface/agents-course` (web) | T1, K2 |

**R10, H1, H2 have no external reference** — they are internal-only work. Say so
in the prompt rather than leaving the session to wonder.

To retrofit a session already in flight, paste the mapping above plus: *"Read the
matching file, then tell me what you'd port as-is, what you'd adapt to our seams,
and what does not apply — before any plan."*

---

## R10 — Remove the legacy SQLite stack

```
Ticket R10 in docs/superpowers/plans/2026-08-02-completion-roadmap.md. Read that
ticket and docs/superpowers/plans/2026-07-14-ticket-legacy-sqlite-removal.md.
No external reference implementation for this one — it is internal-only work.

Set up, then stop:
1. git worktree add ../sc-r10 -b chore/r10-sqlite-removal main
2. cd ../sc-r10 && npm install --ignore-scripts   # better-sqlite3 gyp fails on Node 26
3. Own database (all sessions share one Postgres; vitest is single-worker because
   suites race on catalog resets — see the comment in vitest.config.ts):
   docker compose -f ../Sourcecado/docker-compose.yml up -d
   docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
     psql -U sourcecado -d postgres -c 'CREATE DATABASE sourcecado_r10;'
   cp ../Sourcecado/.env.local .env.local
   IMPORTANT: vitest does NOT read .env.local (no setupFiles in vitest.config.ts).
   Source it into the shell before every test run, or the suite fails ~199 tests
   with "DATABASE_URL is not set":  set -a; source .env.local; set +a
   then point DATABASE_URL and POSTGRES_DB at sourcecado_r10
4. npm run migrate && npm test 2>&1 | tail -30   # record the baseline

Orient: src/lib/db.ts, package.json, tests/db.test.ts, and
grep -rl better-sqlite3 src tests scripts.

Then STOP. No plan, no edits. Run /interview-me and settle scope with me: what
gets deleted, what in scripts/ is still load-bearing, and what the suite should
look like afterward.
```

---

## R11 — Per-run budget + enrich hardening

```
Ticket R11 in docs/superpowers/plans/2026-08-02-completion-roadmap.md. Read that
ticket. Then read the reference implementation:
/Users/fisher/Documents/Good-Examples/claude-code-main/src/query/tokenBudget.ts

Set up, then stop:
1. git worktree add ../sc-r11 -b feat/r11-run-budget main
2. cd ../sc-r11 && npm install --ignore-scripts
3. Own database (all sessions share one Postgres; vitest is single-worker):
   docker compose -f ../Sourcecado/docker-compose.yml up -d
   docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
     psql -U sourcecado -d postgres -c 'CREATE DATABASE sourcecado_r11;'
   cp ../Sourcecado/.env.local .env.local
   IMPORTANT: vitest does NOT read .env.local (no setupFiles in vitest.config.ts).
   Source it into the shell before every test run, or the suite fails ~199 tests
   with "DATABASE_URL is not set":  set -a; source .env.local; set +a
   then point DATABASE_URL and POSTGRES_DB at sourcecado_r11
4. npm run migrate && npm test 2>&1 | tail -30   # record the baseline

Orient: src/lib/agent-loop.ts, src/lib/harness.ts, src/lib/ledger.ts (where
model_calls and tool_calls record usage), src/lib/tools/web-fetch.ts, and
gh pr view 18 --comments for the two open NEEDS-HUMAN security threads.

Then STOP. No plan, no edits. Run /interview-me and settle scope with me: where
the budget check belongs in the loop, whether the ceiling is tokens or dollars
or tool-calls, what happens to a run that hits it, and whether the DNS-rebinding
pin is in scope (it needs the undici dependency — that is a stop-condition, do
not add it without deciding together).
```

---

## H1 — Google OAuth login

```
Ticket H1 in docs/superpowers/plans/2026-08-02-completion-roadmap.md. Read that
ticket, plus the "Stage H" note in
docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md.
No external reference implementation for this one — it is internal-only work.

Confirm PR #20 (R8) is merged into main before starting. If git log main does not
show it, stop and tell me.

Set up, then stop:
1. git worktree add ../sc-h1 -b feat/h1-google-oauth main
2. cd ../sc-h1 && npm install --ignore-scripts
3. Own database (all sessions share one Postgres; vitest is single-worker):
   docker compose -f ../Sourcecado/docker-compose.yml up -d
   docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
     psql -U sourcecado -d postgres -c 'CREATE DATABASE sourcecado_h1;'
   cp ../Sourcecado/.env.local .env.local
   IMPORTANT: vitest does NOT read .env.local (no setupFiles in vitest.config.ts).
   Source it into the shell before every test run, or the suite fails ~199 tests
   with "DATABASE_URL is not set":  set -a; source .env.local; set +a
   then point DATABASE_URL and POSTGRES_DB at sourcecado_h1
4. npm run migrate && npm test 2>&1 | tail -30   # record the baseline

Orient: src/lib/memory/actor.ts (the DEFAULT_ACTOR this replaces), src/app/layout.tsx,
src/lib/chat/sessions.ts, src/migrations/ (numbering), and src/lib/nav.ts.

This is ~2 sessions of work. Do not try to do it all.

Then STOP. No plan, no edits. Run /interview-me and settle scope with me: which
auth library (or none), where sessions live, how DEFAULT_ACTOR gets retired
without breaking existing rows, whether Gmail scopes are requested now or later,
and how you would split this across two sessions.
```

---

## H2 — Team tenancy

```
Ticket H2 in docs/superpowers/plans/2026-08-02-completion-roadmap.md. Read that
ticket. No external reference implementation — internal-only work.
H1 must be merged first — confirm it is on main before starting.

Set up, then stop:
1. git worktree add ../sc-h2 -b feat/h2-tenancy main
2. cd ../sc-h2 && npm install --ignore-scripts
3. Own database (all sessions share one Postgres; vitest is single-worker):
   docker compose -f ../Sourcecado/docker-compose.yml up -d
   docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
     psql -U sourcecado -d postgres -c 'CREATE DATABASE sourcecado_h2;'
   cp ../Sourcecado/.env.local .env.local
   IMPORTANT: vitest does NOT read .env.local (no setupFiles in vitest.config.ts).
   Source it into the shell before every test run, or the suite fails ~199 tests
   with "DATABASE_URL is not set":  set -a; source .env.local; set +a
   then point DATABASE_URL and POSTGRES_DB at sourcecado_h2
4. npm run migrate && npm test 2>&1 | tail -30   # record the baseline

Orient: every CREATE TABLE in src/migrations/, then every query site —
src/lib/memory/*.ts, src/lib/ledger.ts, src/lib/chat/sessions.ts,
src/lib/contacts/*.ts. Report back how many tables and how many query sites you
count; that number decides the split.

This is ~2 sessions. A single missed query site is a cross-tenant data leak, so
the enforcement mechanism matters more than the column.

Then STOP. No plan, no edits. Run /interview-me and settle scope with me:
whether scoping is enforced by Postgres RLS, a wrapped db client, or discipline
plus tests; what happens to existing rows; and how you would split this.
```

---

## T1 — Cut the teaching skeleton

```
Ticket T1 in docs/superpowers/plans/2026-08-02-completion-roadmap.md. Read that
ticket and the coupling-model + course-spine sections of
docs/superpowers/specs/2026-08-02-completion-and-curriculum-design.md.

Reference for course structure and how assignments are graded: the Hugging Face
agents course (github.com/huggingface/agents-course) — units, hands-on per unit,
final project scored on a leaderboard.

R10, R11, H1, and H2 must all be merged first — confirm before starting.

This produces a SEPARATE repo, not a branch of this one. Do not modify
production main.

Set up, then stop:
1. git worktree add ../sc-t1 --detach main     # read-only reference copy
2. cd ../sc-t1 && npm install --ignore-scripts
3. No database needed yet — you are reading, not running.

Orient: map what students build vs what they receive. Students build auth
(Unit 2), agent loop + harness + provider adapters (Unit 3), tool registry +
orchestrator + memory retrieval (Unit 4), eval harness (Unit 5). Everything else
stays. For each removal, find every file that imports it — the import graph
decides whether a clean stub is even possible.

Report back: the file list per unit, and any place where removing student code
would break retained code.

Then STOP. No plan, no edits, no new repo yet. Run /interview-me and settle
scope with me: how the skeleton repo is created and kept in sync, whether stubs
are empty files or typed no-ops, what each unit's failing suite asserts, and
whether one session is remotely enough.
```

---

## Batch 2 (not yet written)

H3 deploy + CI · H4 monitoring · J1 persona · J2 todo/plan · J3 ask_user +
grants. Write after Phase 0 lands, when the freeze baseline is real.
