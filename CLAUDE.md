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

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available gstack skills:
/office-hours, /plan-ceo-review, /plan-eng-review, /plan-design-review, /design-consultation, /design-shotgun, /design-html, /review, /ship, /land-and-deploy, /canary, /benchmark, /browse, /connect-chrome, /qa, /qa-only, /design-review, /setup-browser-cookies, /setup-deploy, /setup-gbrain, /retro, /investigate, /document-release, /document-generate, /codex, /cso, /autoplan, /plan-devex-review, /devex-review, /careful, /freeze, /guard, /unfreeze, /gstack-upgrade, /learn

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
