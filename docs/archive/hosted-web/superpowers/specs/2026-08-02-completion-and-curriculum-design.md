# Sourcecado Completion + Curriculum Design

Date: 2026-08-02
Status: approved (design), roadmap reconciliation in progress

Two programs run off this document: finishing Sourcecado as a hosted product for
real Codeology sourcing directors, and teaching a cohort of new engineers to
build it. They are coupled, and the coupling is the point.

## Locked decisions

- **Product bar:** real Codeology sourcing directors, hosted. Not a demo, not
  internal-only.
- **Cohort:** 3-6 students, 8-12 weeks, starting in 2-4 weeks.
- **Sequencing:** teachable freeze. A stable baseline is frozen as the
  curriculum target; the advanced product track keeps moving in parallel.
- **V1 must-haves:** weekly routine + run loop, Gmail draft output, Apollo
  sourcing end-to-end, identity resolution + sourcing signals.
- **Eval is promoted from v2 to v1.** The 2026-06-08 design doc defers "test
  contact suites and eval prep" to v2. That conflicts with the curriculum, which
  teaches eval as a first-class topic, and with running real credit-spending
  runs against real directors. See K1/K2.
- **Subagents are in scope**, against the 2026-06-08 doc's "no complex
  multi-agent orchestration" exclusion. Rationale in J4.

## The real gate date

The cohort start is not the deadline. Module 1 (AI coding craft) does not touch
the Sourcecado codebase in earnest, so the binding date is **when students open
their first Sourcecado PR, ~4-6 weeks out.**

## Coupling model

### Students trail completed work, not in-flight work

Students build the agent runtime, orchestration, memory, auth, and eval. All of
that is already built in production or nearly so. The in-flight product loop
(routines, Gmail drafts, artifacts) never gates the cohort — it becomes advanced
material and Unit 6 territory.

Consequence: **the freeze gate is small.** It needs the runtime leftovers closed
(R10, R11), auth built as the Unit 2 reference answer (H1, H2), and the skeleton
cut (T1). Nothing else blocks the cohort.

### The skeleton is a subtraction, not a snapshot

Handing students production-at-freeze means auth already exists and they never
build it — but auth is the best Unit 2 lesson available (OAuth, sessions,
tenancy, migrations, protected routes in one slice).

So: skeleton = production at freeze, with the features students should build
**surgically removed** and replaced by a stub plus **a failing test suite**.

That failing suite is the assignment. It gives automated correctness grading for
free, and it is how the Hugging Face agents course grades.

### Assignments are graded two different ways, and that is the lesson

- Units 0-4: **failing tests go green.** Deterministic.
- Units 5-6: **eval score on a shared set.** Non-deterministic.

The whole course lands on one sentence: *traditional software is deterministic
and a test proves it works; an agent is not, and evals are what replace the
test.* Students only feel that because they built both halves themselves.

### Slice cards

Implementation plans in `docs/superpowers/plans/` are source material, not
lessons — they assume deep context. Every production slice gets a short
companion at merge time: what problem it solved, what a student must know first,
the 3-5 decisions worth teaching, and the trap actually hit. ~20 minutes while
context is hot. Melanie expands these into lessons.

## Course spine

| Unit | Topic | ~Wks | Graded by |
|---|---|---|---|
| 0 | Setup, GitHub, branches, PRs, review, CI | 1 | First merged PR |
| 1 | Responsible AI coding — prompting, context engineering, skills, MCP, reviewing AI output | 1-1.5 | A real PR shipped with AI, defended in review |
| 2 | Traditional half — Postgres + migrations, API routes, OAuth, frontend, CI/CD | 2-2.5 | Failing tests → green |
| 3 | Agent runtime — the loop, provider adapters, tool-call cycle, streaming | 2 | Failing tests → green |
| 4 | Orchestration + memory — tool registry, scoped execution, retrieval, citations | 2 | Failing tests → green |
| 5 | Eval + observability — traces, the ledger, building an eval harness | 1.5 | Eval score on shared set |
| 6 | Final project — their own feature, with its own eval | 1.5-2 | Leaderboard + PR review |

~11-12 weeks. Unit 2 closes on "every behavior here is provable by a test."
Unit 3 breaks that. Unit 5 resolves it.

Through-line worth naming explicitly: Unit 1 teaches students to manage their
own context; Unit 4 teaches them to make the agent manage its own (J4).

## Reference systems

Patterns to port rather than invent. Both repos were read directly.

| Need | Reference | Note |
|---|---|---|
| Per-run cost stop | `claude-code-main/src/query/tokenBudget.ts` | `checkTokenBudget()` — 0.9 threshold plus diminishing-returns detection |
| Approval gate | `openworker/coworker/tools/ask.py` | Engine intercepts, run suspends, answer returns as tool result |
| Standing scoped grants | `openworker/coworker/automation/models.py` `grant_entries()` | `"tool target"` entries, write-only, fail-closed, revoked with the automation |
| Planning / todo | `openworker/coworker/tools/todo.py` (87 LOC) + `plan.py` (43 LOC) | 130 lines total |
| Scheduler | `openworker/coworker/automation/scheduler.py` | run-once-catch-up + skip-on-overlap |
| Persona layer | `openworker/coworker/agents/base.py` | `Agent(system_prompt, tool_factory, family, ...)` |
| Subagent | `openworker/coworker/tools/subagent.py` (138 LOC) | Child engine, read-only slice, only the report returns, no recursion |
| Course structure | `huggingface/agents-course` | Units, hands-on per unit, final project graded on a leaderboard |

## Open items

- **PR #21 discarded 2026-08-02 as unusable.** Orgs, contacts, identity
  resolution, outreach history, and the Contact Profile Card return to the build
  list as a B1 rebuild (~3 sessions, early Phase 2). Its branch survives as
  reference only. Carry forward one design idea: identity resolution never
  guesses — an exact canonical-name or alias match resolves, and a shared name
  returns `ambiguous` with candidates instead of silently picking one.
- **Only blocker left: merge PR #20.** With #21 gone there is no migration-006
  collision and no renumbering. #20 touches `package.json`, so branches should
  base off a `main` that contains it.
- **R9** (live two-provider proof) is cut for now — blocked on provider billing,
  not code. The adapter seam stays covered by unit tests only. Re-run as a
  one-off when Anthropic billing clears.
