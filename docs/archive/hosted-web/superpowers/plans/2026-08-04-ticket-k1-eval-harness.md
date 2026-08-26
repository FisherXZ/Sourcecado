# Ticket — K1: agent eval harness over the run ledger

Date: 2026-08-04
Roadmap: K1 in `docs/superpowers/plans/2026-08-02-completion-roadmap.md`
Est: 1.5

## Why now

On 2026-08-04 the agent failed a real sourcing task in a specific, reproducible
way: two tool calls, six seconds, a placeholder template, zero citations. Nobody
noticed for weeks because **462 unit tests were green the entire time.**

That is the gap this closes. The unit suite proves the plumbing executes. It
cannot tell you the agent gave a bad answer. Every fix queued right now —
tool chaining, step budget, parallel execution, the Apollo mapping — is being
made on the strength of one manual run. Without a harness, the next regression
is found the same way: by accident.

The 2026-06-08 design doc defers eval to v2. That was overturned 2026-08-02:
eval is a first-class curriculum topic (Unit 5) and, with Apollo now live and
spending real credits, it is also how regressions get caught before they cost
money.

## What makes this cheap

The substrate already exists. `runs`, `run_steps`, `model_calls`, and
`tool_calls` record every step, tool call, argument, result, and token
(`001_run_ledger_model_gateway.sql`). An eval does not need new instrumentation
— it needs to run a fixed question set and assert over rows that are already
being written.

## Scope

**A golden set of sourcing questions**, seeded from real failures. Case #1 is
the 2026-08-04 probe verbatim: *"Find recruiters or talent leads at Anthropic
worth reaching out to, and draft a short intro email to the best one."*

**Assertions over the ledger, not over prose.** Grading generated text on exact
match is brittle and grading it by model is a later problem. Start with
structural assertions the ledger answers directly:

- which tools were called, and in what order (did it chain, or stop at one?)
- how many steps were used, and whether the run was truncated
- whether the answer carries citations, and whether any are invalid
  (`invalidCitations` is already returned by `answerWithMemory`)
- whether the deliverable contains placeholder tokens (`[Your Name]`)

**A pass/fail run plus a stored score**, so two runs are comparable. This is also
Unit 6's leaderboard (K2).

## Explicitly out of scope

- LLM-as-judge scoring. Later; structural assertions first.
- Live vendor calls on every run. The harness must be runnable against recorded
  fixtures, or it will burn Apollo credits and Tavily quota on every CI run and
  get switched off. Live mode stays opt-in behind `SOURCECADO_RUN_LIVE_SMOKE`,
  matching the existing convention.

## Notes for implementation

- Reference: the Hugging Face agents course grades its final project on a shared
  set with a leaderboard. Same shape.
- Keep the harness out of the default `npm test` path. A red eval means the agent
  got worse, which is different information from a red unit test, and mixing them
  makes both easier to ignore.

## Done when

- The 2026-08-04 failure is encoded as a case that **fails against current
  `main`** and passes once tool-chaining lands.
- The harness runs offline against fixtures by default, live only on opt-in.
- Two runs produce comparable scores, so a regression is visible without reading
  transcripts.
