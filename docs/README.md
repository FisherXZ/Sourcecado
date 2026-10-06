# Documentation map

Sourcecado's dated documents record several product shapes. They are intentionally preserved, but not every old plan is still an instruction.

## Current source of truth

- [Sourcing director spring specification](superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md) — current product job, use cases, scope, and locked conditions
- [Root README](../README.md) — setup, commands, repository map, and local-state policy
- [Your first contribution](../CONTRIBUTING.md) — isolated setup, browser/native development, code map, and first-PR workflow
- [Agent context](../AGENTS.md) — engineering guardrails and documentation precedence
- [Domain context](domain.md) — current sourcing language
- [Design system](design.md) — active visual direction
- [Active and deferred backlog](BACKLOG.md) — the eight approved issues and the work set aside
- [Brand assets](../brand/README.md) — owl mascot masters and icon regeneration

## Course material

The course uses Sourcecado as the shared codebase for learning. Product needs
supply student work, but course terms never redefine product concepts. Product
words live in [domain.md](domain.md); learning and collaboration terms live in
[the course context](course/CONTEXT.md).

- [Course context](course/CONTEXT.md) — canonical learning-work, learning-track, environment, and collaboration terms
- [Course plan](course/COURSE_PLAN.md) — nine-week structure, five Teaching Weeks with active building, four Open Build Weeks, and demo expectations
- [Teaching decks](course/TEACHING_DECKS.md) — full audience-facing slide copy for the five Teaching Weeks
- [Guided Ticket template](course/TICKET_TEMPLATE.md) — outcome, boundaries, verification, live behavior, and PR requirements for future tickets
- [Pull-request template](../.github/pull_request_template.md) — verification, AI Accountability Note, safety, and peer-review contract

## Engineering guides

The [engineering index](engineering/README.md) maps the active backend and frontend. These references describe implementation; the product spec takes precedence.

### How the backend works

- [Agent runs](engineering/agent-runs.md)
- [Run ledger](engineering/run-ledger.md)
- [Run budgets](engineering/run-budgets.md)
- [Effective tools](engineering/effective-tools.md)
- [Compaction](engineering/compaction.md)
- [Context projection](engineering/context-projection.md)
- [Evidence envelope](engineering/evidence-envelope.md)
- [Evaluations](engineering/evaluations.md)

### Sourcing job seams

- [Approved send](engineering/approved-send.md)
- [Reply filing](engineering/reply-filing.md)
- [Durable person tasks](engineering/person-tasks.md)
- [Living brief](engineering/living-brief.md)
- [Meeting evidence](engineering/meeting-evidence.md)
- [Legal artifacts](engineering/legal-artifacts.md)

### Operate and ship

- [Onboarding documentation upkeep](engineering/onboarding-docs-upkeep.md) — scope of living-guide corrections and setup verification limits
- [Doctor](engineering/doctor.md)
- [Secret scan](engineering/secret-scan.md)
- [Diagnostic bundle](engineering/diagnostic-bundle.md)
- [Packaging](engineering/packaging.md)
- [Preview updates](engineering/update-channel.md)

### ADRs

- [0001 Manual run does not consume the weekly slot](engineering/adr/0001-manual-run-does-not-consume-weekly-slot.md)
- [0002 Workspace runtime](engineering/adr/0002-sourcecado-workspace-runtime.md)
- [0003 macOS preview packaging](engineering/adr/0003-macos-preview-artifact-packaging.md)

## Current execution records

- [September 21 onboarding architecture review](audit/2026-09-21-onboarding-architecture-review.md) — authority paths, restart boundaries, database policy, and proposed backlog dispositions
- [September 21 onboarding correctness review](audit/2026-09-21-onboarding-correctness-review.md) — confirmed product gaps and bounded verification
- [September 21 reconciled onboarding backlog](audit/2026-09-21-onboarding-backlog.md) — current issue dispositions and next work
- [September 21 setup verification](audit/2026-09-21-onboarding-setup-verification.md) — reproduced setup failures, fixes, test results, and verification limits
- `superpowers/plans/2026-08-24-*`, `superpowers/plans/2026-08-25-*`, and `superpowers/plans/2026-08-27-*` — local runtime, sourcing spring, desktop UI, and prompt/context plans
- `superpowers/plans/2026-08-28-documentation-upkeep.md` — the August documentation pass
- `qa/` — current end-to-end and visual QA evidence
- [DU-01 ExternalStore go/no-go](../frontend/EXTERNAL_STORE_GO_NO_GO.md) — dated proof from 2026-08-25, not a living guide

These are dated working records. Check their status and the current code before treating an unchecked item as outstanding.

## Release and historical records

- [Release records](releases/README.md) preserve the changelog and legacy hosted version.
- [UI explorations](archive/ui-explorations/) preserve the former root scratchpad designs.

Dated records may use the former paths: `desktop/coworker/` is now
`backend/coworker/`, `desktop/surfaces/gui/` is now `frontend/`, and
`desktop/docs/` is now `docs/engineering/`. Their claims describe the revision
reviewed at the time; use current guides for commands.

Pre–August 24 hosted/runtime material now lives under `archive/hosted-web/` in the same internal categories: ADRs, designs, intent, grill sessions, specs, and plans.

They describe the original memory CLI, hosted Next.js/Postgres architecture, weekly autonomous sourcing loop, and runtime-solidification work. When they conflict with the 2026-08-25 sourcing-director specification, they are prior art—not current scope.

## Archive policy

Historical documentation stays readable and dated rather than being rewritten to sound current. See the [historical documentation note](archive/hosted-web/README.md) and [code archive policy](../archive/README.md). New docs should link to active paths and state whether they are a source of truth, a proposal, an execution record, or historical evidence.
