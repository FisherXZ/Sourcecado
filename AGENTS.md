# Sourcecado Agent Context

## High-Level Product Direction

Sourcecado is becoming a hosted team sourcing operating system for Codeology.
The memory layer is pillar one: it preserves contacts, sourcing history,
source citations, knowledge gaps, outcomes, and human feedback. Pillar two is
an autonomous sourcing agent that tells Sourcing Directors what to do next and
produces review-ready work.

The two-month direction is a weekly sourcing loop: configure a routine, pull or
enrich contacts through Apollo, research with Gmail/Drive/Notion/web context,
resolve identities, record sourcing signals, rank who to work, create Gmail
drafts, record usage in the run ledger, capture human feedback, and feed that
back into memory.

Sourcecado owns its own domain runtime and should borrow patterns from
OpenClaw and Hermes for tools, routines, ledgers, and memory loops. Do not make
OpenClaw, Hermes, or MCP the core dependency for the near-term build.

## Roadmap Guardrails

- Build a hosted team app, not just a local CLI.
- Prioritize agent/tool orchestration and memory architecture over app polish.
- Use real Apollo API, Gmail drafts, web search, Google Drive, and Notion where
  practical.
- Keep Apify in the connector boundary, but defer LinkedIn/Apify v2.
- Create Gmail drafts only; actual sending and approve-to-send are deferred.
- Store run steps, tool calls, artifacts, source refs, feedback, and rationale
  summaries.
- Treat sourcing signals and identity resolution as v1 primitives.
- Track Apollo credits, web calls, Apify runs, Gmail quota-sensitive actions,
  and model usage in the run ledger.
- Keep the first UI to routine setup, run result, and run contact. Gmail draft
  review can be a popup; memory can live under settings.
- Defer formal routine/agent versioning, test contact suites, and eval prep to
  v2.

For the current full design, read
`docs/superpowers/specs/2026-06-08-sourcecado-autonomous-sourcing-os-design.md`.
