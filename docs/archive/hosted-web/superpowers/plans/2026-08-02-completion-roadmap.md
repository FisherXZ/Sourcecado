# Completion Roadmap — reconciled

Date: 2026-08-02
Design: `docs/superpowers/specs/2026-08-02-completion-and-curriculum-design.md`

This roadmap **extends** the existing taxonomy in
`docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`
(F / A / B / C / D / E / G stages). It does not replace it. New stages use
letters that were free: **H** (hosting & access), **J** (agent depth),
**K** (eval), **T** (teaching). Runtime-sprint leftovers keep the **R** prefix.

Do not introduce a second ID scheme. If a ticket already exists in the
2026-06-15 breakdown, use its ID.

## Status of the existing roadmap

| Stage | State |
|---|---|
| F1-F5, FD | Done (2026-06-18 → 06-23) |
| A1-A3, A5, A6, A7.1 | Done (PR #8) |
| A4, A7.2 | Deferred |
| R0-R8 | Done; **R8 open as PR #20** |
| R9 | **Cut** — blocked on provider billing, not code |
| B1 | **Outstanding — rebuild.** PR #21 discarded 2026-08-02 as unusable |
| B2-B5, C1-C2, D1-D4, E1-E2, G1-G3 | Outstanding |

**B1 (orgs, contacts, aliases, identity resolution, outreach history, Contact
Profile Card) is back on the build list.** PR #21 attempted it and was rejected;
its branch `feat/b1-contacts` is kept only as reference. Nothing from it is on
`main`, and its migrations `006/007/008` were never applied — so numbering after
R8's `006_note_provenance.sql` is clean from `007` onward.

One design idea from that attempt is worth carrying into the rebuild regardless
of the code: **identity resolution should never guess.** An exact canonical-name
or alias match resolves; two people or orgs sharing a name return `ambiguous`
with candidates rather than silently picking one.

## Blocking issue — resolve before any branch is cut

`main` is at `372d613` (R7). **One open PR: #20** (`feat/rt-r8-ai-sdk-ripout`),
adding `006_note_provenance.sql`. With PR #21 discarded there is no migration
collision and no renumbering.

**Merge PR #20, then branches can be cut.** It touches `package.json` (dropped
`@ai-sdk/*`, added `package-deps.test.ts`), which is why R10 in particular
should base off a `main` that already contains it. PR #20 was fully gated: 440
passed, zero new failures, tsc and build clean.

Recommend closing PR #21 on GitHub so it stops appearing as in-flight work.

## Worktree isolation — required

`vitest.config.ts` forces `maxWorkers: 1` with an explicit comment: all suites
share one database, and parallel workers race on Postgres catalog resets
(80-118 flaky failures observed). **Two sessions testing at once against the
same database will reproduce that.**

Every session therefore creates its own database on the shared Postgres
container and points `.env.local` at it. Steps are in each session prompt.

---

# Phase 0 — Freeze gate (~5.5 sessions)

The only work standing between now and the cohort.

## R10 — Remove the legacy SQLite stack

**Goal:** Delete the better-sqlite3 CLI stack and its 9 test files.
**Why now:** All 78 suite failures come from these files (bindings were never
compiled; `npm install --ignore-scripts` on Node 26). Removing them turns the
suite green, which every later ticket's baseline depends on.
**Depends on:** nothing — touches no migrations, can start before the PR #20/#21
merge resolves.
**Prior art:** `docs/superpowers/plans/2026-07-14-ticket-legacy-sqlite-removal.md`
(resolution decided 2026-07-14: delete post-sprint).
**Done when:** `npm test` shows zero failures; `better-sqlite3` and
`@types/better-sqlite3` gone from `package.json`; `npm install` works without
`--ignore-scripts`; `tsc --noEmit` and `npm run build` clean.
**Est:** 0.5
**Status: done 2026-08-03** on `chore/r10-sqlite-removal`. 436 passed / 0
failed / 2 skipped across 66 files; clean `npm install`, `tsc --noEmit`, and
`npm run build`. Deferred out: moving the 8 surviving `src/*.ts` files into
`src/lib/` (own ticket; T1 owns student-facing repo shape).

## R11 — `web_fetch` hardening

**Rescoped 2026-08-04.** This ticket originally bundled a per-run spend ceiling
with two security fixes. The ceiling was split out to
`2026-08-04-ticket-enrich-spend-ceiling.md`, which carries the settled design and
the full reasoning; it now sits with J3/J4, where its consumers are. In short:
Apollo is not live, the `enrich` permission class is already the gate, and the
ceiling protects an autonomous path that does not exist yet.

**Goal:** Close the two security items PR #18 shipped deferred.
**Why now:** Both are real today, unblocked, and depend on nothing else. Phase 0
should not carry known findings into a teaching freeze.
**Depends on:** R10 (clean baseline) — nominally; neither fix touches the legacy
SQLite files.
**In scope:**
- `web_fetch` response-size cap applied *during* streaming. Today
  `src/lib/tools/web-fetch.ts` calls `res.text()`, buffering the whole body
  before slicing to `WEB_FETCH_MAX_CHARS` — so the cap bounds the string, not
  memory.
- DNS-rebinding IP-pin. `assertPublicHost` validates the lookup, then `fetch`
  re-resolves at connect time.

**Approach — no new dependency.** PR #18 assumed the pin needed `undici` (a
runbook stop-condition). It does not: `https.request` accepts a `lookup` option
reaching `tls.connect`, which pins the connection to the validated address while
SNI and certificate validation still use the hostname. Verified by spike
2026-08-04: pinned to `127.0.0.1` against a `CN=pinned.test` cert, TLS validated;
`req.destroy()` at the cap stopped the transfer at 262KB of a 40MB body. The
byte cap forces a stream anyway, so both findings collapse into one rewrite.
`undici` remains the fallback and would need explicit approval.

**Open threads:** PR #18 findings #2 and #3. Finding #7 (`add_memory_note`
runId/actor stamp) is already resolved on `main` and is not in scope.
**Done when:** a rebinding stub that resolves public-then-private is refused at
connect time, not just at lookup; an oversized body is aborted mid-stream without
being buffered; tests cover both.
**Est:** 0.5

## H1 — Google OAuth login

**Goal:** Real user login. Replace the single shared `DEFAULT_ACTOR`.
**Why now:** Hosted product with real directors. Also the Unit 2 reference
answer — students build this, so it must exist first.
**Why Google specifically:** Gmail drafts (Stage D/E work) need Google OAuth
regardless. Building it once and widening scopes later avoids a second identity
system and saves roughly a session.
**Depends on:** PR #20/#21 merge resolution.
**Prior art:** the 2026-06-15 breakdown calls this **"Stage H — per-officer
login/attribution"**, specced but deferred. This ticket is that stage.
**Touches:** `src/lib/memory/actor.ts` (`DEFAULT_ACTOR`), new `users` table,
session handling, protected routes.
**Done when:** a director signs in with Google; runs, notes, and sessions are
attributed to their user id, not `DEFAULT_ACTOR`.
**Est:** 2 — agent proposes the split during `/interview-me`.

## H2 — Team tenancy

**Goal:** `team_id` on every tenant-scoped table, with query scoping enforced.
**Why now:** "Hosted team app" is a locked decision, and retrofitting tenancy
after data exists is far more expensive than before.
**Depends on:** H1.
**Current state:** zero tenancy — no `user_id` or `team_id` in any migration.
**Watch:** every existing query in `src/lib/memory/*` and `src/lib/ledger.ts`
needs scoping. Missing one is a cross-tenant data leak, so the enforcement
mechanism matters more than the column.
**Done when:** a user in team A cannot read team B's contacts, memory, runs, or
sessions, proven by test.
**Est:** 2 — agent proposes the split during `/interview-me`.

## T1 — Cut the teaching skeleton — DEFERRED TO LAST (2026-08-04)

**Deferred by Fisher 2026-08-04: T1 moves out of Phase 0 to the end of the
program.** Consequence, recorded deliberately: the teachable-freeze model no
longer has a ~4-6 week gate, because students cannot open a Sourcecado PR until
the skeleton exists. The curriculum's build-along either starts late or runs
against production `main` directly, which is a different course (students read
code rather than build it) and would need Units 2-5 reworked.

Also now homeless: R10's deferred cleanup — moving the 8 surviving `src/*.ts`
files into `src/lib/` — was assigned here because T1 owned repo shape. It needs
its own small ticket (~0.5).

**Goal:** The student repo — production at freeze, with the code students build
removed and replaced by stubs plus failing test suites.
**Why it was Phase 0:** gated the cohort's first PR.
**Depends on:** R10, R11, H1, H2 (H1/H2 are the Unit 2 reference answer). Note
R11 no longer contains budget enforcement (rescoped 2026-08-04) — if the
skeleton wants a budget assignment, it depends on
`2026-08-04-ticket-enrich-spend-ceiling.md` instead.
**Removed and assigned to students:** auth (Unit 2), agent loop + harness +
provider adapters (Unit 3), tool registry + orchestrator + memory retrieval
(Unit 4), eval harness (Unit 5).
**Retained in the skeleton:** Next.js scaffold, DESIGN.md + `src/components/ui`,
db client + migration runner, ledger, chat UI shell, test infrastructure, CI.
**Done when:** the skeleton installs, migrates, and runs; every assignment's
suite fails for the right reason; production `main` still passes its own suite.
**Est:** 1 — likely more; agent proposes the split during `/interview-me`.

---

# Phase 1 — Production hardening (~9.5 sessions)

Runs during cohort weeks 1-8. K1 must land before Unit 5 (~cohort week 9).

| ID | Ticket | Est | Notes |
|---|---|---|---|
| H3 | Deploy + managed pgvector + secrets + CI | 1 | No `.github/workflows` exists today; no deploy config |
| H4 | Error monitoring | 0.5 | |
| J1 | Sourcing persona + system prompt | 1 | Plan already written: `2026-07-15-sourcing-agent-system-prompt.md` |
| J2 | `todo_write` + `propose_plan` tools | 1 | Port `openworker/coworker/tools/{todo,plan}.py` — 130 LOC total |
| J3 | `ask_user` + scoped standing grants + **real Gmail drafts** | 3.5 | Port `ask.py` + `automation/models.py` `grant_entries()`. Gmail draft output pulled forward from Phase 2 (Fisher, 2026-08-04) — J3 ships the gate with a real consumer instead of a stand-in |
| J4 | `research_contact` subagent | 1.5 | See below |
| K1 | Golden contact set + eval runner over ledger traces | 1.5 | Unit 5 reference answer |
| K2 | Regression scoreboard | 0.5 | Also the Unit 6 leaderboard |
| E1 | Routine + Playbook model *(existing ticket)* | 2 | Port `openworker/coworker/automation/` — run-once-catch-up + skip-on-overlap |

## J4 — `research_contact` subagent (detail)

Included over the 2026-06-08 doc's "no complex multi-agent orchestration"
exclusion, because per-contact research is a genuinely different case from
generic multi-agent work.

Reference: `openworker/coworker/tools/subagent.py`. Four properties map cleanly:

| OpenWorker `explore` | Sourcecado `research_contact` |
|---|---|
| Child engine, fresh context; only the final report returns | A 20-contact run would otherwise flood the parent context with 20x the tool reads |
| `Mode.PLAN` hard-blocks writes regardless of child intent | Already available: `registry.list(allowed)` with `allowed = {read, enrich}` |
| No recursion — child registry omits the tool | Same one-line omission |
| Low risk → eligible for parallel execution | Per-contact research is embarrassingly parallel |

Trace nesting is free: `run_steps.parent_step_id` already exists and is indexed
(`001_run_ledger_model_gateway.sql`). Child steps hang under the caller's step
with no schema change.

**Hard constraint: the enrich spend ceiling must land first** —
`2026-08-04-ticket-enrich-spend-ceiling.md`, split out of R11 on 2026-08-04.
N parallel researchers multiply Apollo spend, and no human is at the keyboard to
answer a J3 approval prompt. (Token spend is explicitly not a concern.)

---

# Phase 2 — Product loop (~15 sessions)

Cohort weeks 8-16. Mostly existing tickets; re-scope when Phase 1 completes
rather than planning in detail now.

**`B1` orgs + contacts + identity resolution (~3, rebuild)** · `E2` routine page
+ manual run · `C1` Apollo tools live + credit ledger · `D1-D4` artifact system
+ validation + research/lead-list/summary artifacts · Gmail draft output (new
ticket, needs H1 scopes + J3 approval) · `G1` feedback → memory · `G2` usage +
run status visibility · `B4` contact detail page · `G3` seeded demo + end-to-end
smoke.

B1 should come early in Phase 2: `B4` (contact detail page) and much of the
sourcing loop depend on contacts existing.

---

# Agent-quality batch — from the 2026-08-04 live run

A real sourcing task produced 2 tool calls in 6.4s and a placeholder template
with zero citations, while 462 unit tests stayed green. Build in this order —
each one is a precondition for the next being measurable.

| # | Ticket | Est |
|---|---|---|
| 1 | `2026-08-04-ticket-apollo-search-field-mapping.md` — unstarves the agent | 0.25 |
| 2 | `2026-08-04-ticket-loop-step-budget-partial-answer.md` — makes room, and stops exhaustion returning nothing | 0.25 |
| 3 | `2026-08-04-ticket-agent-tool-chaining.md` — makes it chain search → resolve → enrich | 0.25 |
| 4 | `2026-08-04-ticket-k1-eval-harness.md` — encodes the failure so it stays fixed | 1.5 |
| 5 | `2026-08-04-ticket-parallel-tool-execution.md` — wall-clock; gates J4 being worth building | 0.5 |

**~2.75 sessions.** Order is load-bearing:

- **2 before 3.** Chaining makes the agent spend 5-6 steps before it writes
  anything. On today's loop that hits the ceiling and returns an empty string,
  because the exhaustion path drops `finalText`. Shipping 3 first would read as a
  regression.
- **1 before 3.** Chaining is untestable while Apollo returns `name: null` —
  there is nothing to chain *to*.
- **5 interacts with the spend ceiling.** A pre-flight budget check that is
  correct serially can be raced by concurrent `enrich` calls.

# Loose tickets — not in any phase

| Ticket | Est | Note |
|---|---|---|
| `2026-08-04-ticket-default-config-401s.md` | 0.25 | `.env.example` default provider 401s; renders as an empty answer with no explanation. **deepseek + deepseek-v4-flash is the only working, fully-supported config today** |
| `2026-08-04-ticket-gateway-provider-inconsistency.md` | 0.25 | `openai` is accepted by `pickAdapter` but rejected by `callModel` — chat works, memory ingest throws |
| `2026-08-04-ticket-enrich-spend-ceiling.md` | 1 | Split from R11. **Blocks J4 and E1** |
| `2026-07-15-ticket-session-history-cap.md` | 1 | Marked **MUST-FIX before real team use** — resumed sessions outgrow the context window |
| `2026-07-21-ticket-remove-ai-sdk-ui-stream.md` | 0.5 | Finishes the AI SDK rip-out |
| *(unwritten)* move 8 surviving `src/*.ts` into `src/lib/` | 0.5 | Orphaned by T1's deferral; R10 deferred it there |

# Capacity

~33 sessions across all phases (was ~30 before B1 returned to the build list).
Budget ~20% overhead for skeleton work and slice cards → **~39
session-equivalents.** At 3/week that is ~13 weeks; at 5/week, ~8. V1 now lands
slightly after the cohort ends.

Designated cut if the calendar slips: the D1-D4 artifact system. Everything else
is load-bearing for the two-month demo loop.
