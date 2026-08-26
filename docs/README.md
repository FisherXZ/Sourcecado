# Documentation map

Sourcecado's dated documents record several product shapes. They are intentionally preserved, but not every old plan is still an instruction.

## Current source of truth

- [Sourcing director spring specification](superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md) — current product job, use cases, scope, and locked conditions
- [Mature Agent System blueprint](superpowers/specs/2026-08-26-sourcecado-mature-agent-system-blueprint.md) — approved Agent OS architecture basis and primary reference donors
- [Evidence-reconciled Agent OS roadmap](superpowers/plans/2026-08-26-evidence-reconciled-agent-os-roadmap.md) — approved Priority 0–4 implementation direction grounded in live workflow evidence
- [Root README](../README.md) — setup, commands, repository map, and local-state policy
- [Agent context](../AGENTS.md) — engineering guardrails and documentation precedence
- [Domain context](../CONTEXT.md) — current sourcing language
- [Design system](../DESIGN.md) — active visual direction
- [Product TODOs](../TODOS.md) — current gaps and explicit deferrals

## Current execution records

- `superpowers/plans/2026-08-24-*` and `superpowers/plans/2026-08-25-*` — local runtime, sourcing spring, and desktop UI plans
- `superpowers/plans/2026-08-26-product-validation-sprint-roadmap.md` — golden-workflow validation process and current execution status
- `superpowers/plans/2026-08-26-priority-0-durable-agent-run-implementation.md` — dependency-ordered implementation plan for the approved Durable Agent Run
- `qa/2026-08-26-golden-workflow-baseline.md` — real outreach and pitch-package run evidence that drove the Agent OS roadmap
- `qa/` — current end-to-end and visual QA evidence
- `desktop/docs/adr/` — ADRs scoped to the active local runtime

These are dated working records. Check their status and the current code before treating an unchecked item as outstanding.

## Historical records

Pre–August 24 hosted/runtime material now lives under `archive/hosted-web/` in the same internal categories: ADRs, designs, intent, grill sessions, specs, and plans.

They describe the original memory CLI, hosted Next.js/Postgres architecture, weekly autonomous sourcing loop, and runtime-solidification work. When they conflict with the 2026-08-25 sourcing-director specification, they are prior art—not current scope.

## Archive policy

Historical documentation stays readable and dated rather than being rewritten to sound current. See the [historical documentation note](archive/hosted-web/README.md) and [code archive policy](../archive/README.md). New docs should link to active paths and state whether they are a source of truth, a proposal, an execution record, or historical evidence.
