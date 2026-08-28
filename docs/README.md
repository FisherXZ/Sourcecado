# Documentation map

Sourcecado's dated documents record several product shapes. They are intentionally preserved, but not every old plan is still an instruction.

## Current source of truth

- [Sourcing director spring specification](superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md) — current product job, use cases, scope, and locked conditions
- [Root README](../README.md) — setup, commands, repository map, and local-state policy
- [How to contribute](../CONTRIBUTING.md) — setup, verification, pull requests, and language
- [Agent context](../AGENTS.md) — engineering guardrails and documentation precedence
- [Domain context](../CONTEXT.md) — current sourcing language
- [Design system](../DESIGN.md) — active visual direction
- [Product TODOs](../TODOS.md) — current gaps and explicit deferrals
- [Context map](../CONTEXT-MAP.md) — separates the Sourcecado product language from the Sourcecado Course language
- [Brand assets](../brand/README.md) — owl mascot masters and icon regeneration

## Current course design

- [Course context](course/CONTEXT.md) — canonical learning-work, learning-track, environment, and collaboration terms
- [Course plan](course/COURSE_PLAN.md) — nine-week structure, five Teaching Weeks with active building, four Open Build Weeks, and demo expectations
- [Teaching decks](course/TEACHING_DECKS.md) — full audience-facing slide copy for the five Teaching Weeks
- [Guided Ticket template](course/TICKET_TEMPLATE.md) — outcome, boundaries, verification, live behavior, and PR requirements for future tickets
- [Pull-request template](../.github/pull_request_template.md) — verification, AI Accountability Note, safety, and peer-review contract

## Current desktop engineering records

These describe the active local runtime. They are not the product spec. If they conflict with the 2026-08-25 specification, the spec wins.

### How the sidecar works

- [Agent runs](../desktop/docs/agent-runs.md)
- [Run ledger](../desktop/docs/run-ledger.md)
- [Run budgets](../desktop/docs/run-budgets.md)
- [Effective tools](../desktop/docs/effective-tools.md)
- [Compaction](../desktop/docs/compaction.md)
- [Context projection](../desktop/docs/context-projection.md)
- [Evidence envelope](../desktop/docs/evidence-envelope.md)
- [Evaluations](../desktop/docs/evaluations.md)

### Sourcing job seams

- [Approved send](../desktop/docs/approved-send.md)
- [Reply filing](../desktop/docs/reply-filing.md)
- [Living brief](../desktop/docs/living-brief.md)
- [Meeting evidence](../desktop/docs/meeting-evidence.md)
- [Legal artifacts](../desktop/docs/legal-artifacts.md)

### Operate and ship

- [Doctor](../desktop/docs/doctor.md)
- [Secret scan](../desktop/docs/secret-scan.md)
- [Diagnostic bundle](../desktop/docs/diagnostic-bundle.md)
- [Packaging](../desktop/docs/packaging.md)
- [Preview updates](../desktop/docs/update-channel.md)

### ADRs

- [0001 Manual run does not consume the weekly slot](../desktop/docs/adr/0001-manual-run-does-not-consume-weekly-slot.md)
- [0002 Workspace runtime](../desktop/docs/adr/0002-sourcecado-workspace-runtime.md)
- [0003 macOS preview packaging](../desktop/docs/adr/0003-macos-preview-artifact-packaging.md)

## Current execution records

- `superpowers/plans/2026-08-24-*`, `superpowers/plans/2026-08-25-*`, and `superpowers/plans/2026-08-27-*` — local runtime, sourcing spring, desktop UI, and prompt/context plans
- `superpowers/plans/2026-08-28-documentation-upkeep.md` — this documentation pass
- `qa/` — current end-to-end and visual QA evidence
- [DU-01 ExternalStore go/no-go](../desktop/surfaces/gui/EXTERNAL_STORE_GO_NO_GO.md) — dated proof from 2026-08-25, not a living guide

These are dated working records. Check their status and the current code before treating an unchecked item as outstanding.

## Historical records

Pre–August 24 hosted/runtime material now lives under `archive/hosted-web/` in the same internal categories: ADRs, designs, intent, grill sessions, specs, and plans.

They describe the original memory CLI, hosted Next.js/Postgres architecture, weekly autonomous sourcing loop, and runtime-solidification work. When they conflict with the 2026-08-25 sourcing-director specification, they are prior art—not current scope.

## Archive policy

Historical documentation stays readable and dated rather than being rewritten to sound current. See the [historical documentation note](archive/hosted-web/README.md) and [code archive policy](../archive/README.md). New docs should link to active paths and state whether they are a source of truth, a proposal, an execution record, or historical evidence.
