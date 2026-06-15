# Sourcecado Autonomous Sourcing OS Weekly Timeline

Date: 2026-06-08 (rescoped)
Status: Scoped for a 2-person team at ~5 hrs/week each (~70-100 person-hours total)

> **For agentic workers:** This is the two-month roadmap and curriculum
> timeline. When turning any week into implementation tasks, use
> `superpowers:writing-plans` for that week's detailed plan, then
> `superpowers:executing-plans` or `superpowers:subagent-driven-development`
> to execute it.

## Why This Was Rescoped

The earlier design doc (`docs/superpowers/specs/2026-06-08-sourcecado-autonomous-sourcing-os-design.md`)
describes the full vision. That vision is scoped for a full-time team
(~300-500 hrs). This team has ~10 combined hours/week for ~10 weeks
(~70-100 person-hours). This timeline cuts the vision down to what is
genuinely achievable and genuinely useful at that capacity, and sequences
the work so the team is never forced to cut at the last minute.

The design doc remains the north star. This timeline is the contract.

## Corrected Product Understanding

Three corrections to the original design doc, based on the real sourcing-director
workflow:

1. **The "brand" is the memory brain.** Codeology has a Google Drive of past
   sourcing directors' notes that nobody reads. Turning it into queryable
   institutional knowledge that the agent and humans both draw on - and that
   everything the agent produces flows back into - is the emotional core of the
   project and the most reusable asset. It is also ~70% built already.
2. **The workflow is company-first, not contact-first.** Directors start from
   target companies, research each company's last ~18 months, then use Apollo
   to find people in target roles (PMs, engineers, eng managers,
   university/recruiting, decision-makers at smaller companies), then draft with
   that context. Organizations are first-class, with company research attached.
3. **The follow-up sequence is central, not a nice-to-have.** A contact enters a
   cadence (initial -> +3d -> +5d, ~2 follow-ups, drop everything if they reply).
   The per-contact timeline and next-action tracking are what justify a
   full-stack app over a CLI: a director needs to see the state of every contact
   and what they owe today.

## Cut Order (protect the floor)

Build in this order so each tier is independently useful and shippable:

- **FLOOR (must ship):** Memory brain loaded with the real Drive notes + a thin
  web app to query it and add/correct notes; agent outputs auto-write back.
  A new director can ask the institutional brain instead of reading 5 semesters
  of docs. Useful alone.
- **TARGET (the real product):** + one playbook -> run -> company research ->
  Apollo people -> personalized drafts -> run-result page -> per-contact timeline
  with follow-up sequence. Manual "Run now." This removes the actual weekly pain.
- **STRETCH (only if ahead):** + recurring scheduling (Mon/Wed/Fri cron),
  LinkedIn recent-activity enrichment, feedback-changes-ranking, polish. These
  are the time-sinks; cut first, lose nothing essential.

The three biggest time traps to defer ruthlessly: **Gmail OAuth, recurring cron
scheduling, and LinkedIn scraping.** None are on the critical path to value.

## Stack Decision

- **Frontend/app:** Next.js (App Router), TypeScript end-to-end.
- **Backend store:** Supabase (Postgres + Auth + Storage + pgvector).
  - Real team login is nearly free via Supabase Auth.
  - Embeddings move from text-in-SQLite to pgvector.
  - Single shared team workspace for v1; defer per-workspace RLS/multi-tenant.
- **The brain:** port the existing memory *model and logic* (source records,
  chunks, entities/aliases, relationships, semantic facts, citations, and the
  Answer/Evidence/Gaps/Next-Action contract) to Supabase Postgres. Do not
  redesign the memory model - reuse the proven design.
- **Operational tables (new, same Postgres):** routines, organizations,
  contacts, contact_identities, outreach_actions, drafts, runs, tool_calls.
- **Agent runtime:** write a small (~200-line) custom tool-use loop on the
  Claude API (Anthropic SDK). Do NOT adopt Hermes/OpenClaw as the harness -
  owning a small loop is more teachable and avoids weeks learning a framework.
  This matches the design doc's "own the runtime" decision.
- **Connectors (buy, don't build):** Apollo (team key) behind a thin adapter;
  web/company research and LinkedIn activity via available MCP sourcing tools if
  the runtime can reach them; Gmail draft creation via the Gmail connector. The
  agent calls these as tools.

## Team Split

- **Person A:** memory port + agent loop + connectors.
- **Person B:** Next.js app + the three pages.
- They meet at the run-result page.

## End State By 2026-08-16

A sourcing director can create a weekly playbook, run it, review ranked contacts
with company research and cited evidence, generate or inspect a Gmail draft, see
each contact's follow-up timeline, give feedback that becomes memory, and trust
that the agent's work was recorded back into the institutional brain.

---

## Review Notes

- The brain ships first because it is the most reusable asset, is already mostly
  built, and is useful standalone - it is the safe floor.
- The loop is built on one company before being generalized, so the team always
  has something running end-to-end.
- Sequences and the contact timeline are treated as core, because they are what
  make the full-stack app worth building over a CLI.
- Gmail OAuth, cron scheduling, and LinkedIn scraping are kept off the critical
  path because they are the most likely to consume the budget without adding
  proportional value.
