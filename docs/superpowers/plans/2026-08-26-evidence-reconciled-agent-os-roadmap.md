# Evidence-Reconciled Agent OS Roadmap

Date: 2026-08-26  
Status: proposed at the product-validation decision gate  
Evidence: `docs/qa/2026-08-26-golden-workflow-baseline.md`

## Product conclusion

Keep the sourcing skills. Do not solve their failures with increasingly long
skill prose. The two runs exposed five shared operating-system capabilities
that should form the next implementation sequence.

## Priority 0: skill-run contract and convergence

### Problem

Both skills researched successfully but required manual cancellation and a
second user turn before delivering the requested outcome.

### Build

- Give each skill invocation one durable run record with skill name, validated
  inputs, source scope, phases, checkpoints, artifact refs, and terminal state.
- Reserve a synthesis/delivery phase before the tool or time budget is spent.
- When the research budget ends, require a useful partial deliverable with
  completed evidence and explicit gaps instead of another tool round.
- Preserve partial assistant output and completed phase state on stop/error.
- Expose current phase and progress in the existing run UI.

### Copy

- OpenWorker: mid-turn checkpoint events, persisted compaction state, partial
  turn preservation, real-file artifact contract.
- Grok Bot: turn checkpoint/settle boundary and transcript/index workers.
- OpenClaw: incomplete-turn recovery, detached task status, attempt/terminal
  records, and final-answer separation from reasoning/tool activity.

### Done when

Each golden skill reaches its director-review deliverable in one run without a
"stop using tools and answer now" follow-up.

## Priority 1: explicit source scope and canonical source registry

### Problem

The pitch run expanded to 155 evidence items and unrelated duplicate SOPs. Long
documents truncated before relevant sections. Current search cannot distinguish
canonical, stale, restricted, and merely matching sources.

### Build

- Resolve and persist an exact source scope before research: folder ids,
  canonical document ids, company/people scope, and allowed connectors.
- Add canonical-source metadata: purpose, semester, version, approval status,
  sensitivity, supersedes/superseded-by, and owner.
- Make Drive folder ingestion/indexing recursive, resumable, idempotent, and
  outside the interactive step budget.
- Query indexed chunks later instead of rereading full source bodies.
- Exclude results outside the selected tree unless the director expands scope.

### Copy

- Grok Bot: worker-backed search index and corruption/rebuild behavior.
- OpenClaw: workspace/source scope, bounded memory indexes, provenance, and
  background-task progress.
- OpenWorker: explicit roots and per-session workspace boundaries.

### Related work

Issue #43 is now directly product-proven. Drive MIME truth and cache fixes from
PR #49 support this path. The broad generic record ontology in PR #47 should
not be accepted wholesale without reconciling it with the person-file product
model.

### Done when

The Zoox run reads only the approved pitch sources and company evidence, and the
outreach run reads only the selected semester/campaign materials.

## Priority 2: first-class deliverables and Google Slides

### Problem

The pitch skill produced strong prose but could not create the actual product:
an editable company deck. Neither workflow emitted a first-class artifact.

### Build

- Add a Sourcecado artifact record with stable id, type, version, run, source
  refs, review status, external/local location, and provenance.
- Add Google Slides read/copy/update behind the existing Google connector:
  inspect template structure, copy an approved template, apply bounded text and
  image edits, and return an editable Slides URL.
- Require approval before creating/copying/updating an external presentation.
- Render deck, campaign-review set, and follow-up package as artifacts, not only
  assistant Markdown.
- Keep Figma out of the critical path initially; record its existing links as
  source refs and migrate the approved template to Google Slides if necessary.

### Copy

- OpenWorker: workspace/scratch artifacts and addressable artifact panel.
- OpenClaw: Workboard artifact/proof metadata and immutable attempt receipts.
- Grok Bot: artifact-aware transcript/checkpoint settlement.

### Done when

The pitch skill produces an editable, source-linked deck from an approved
template, and the outreach skill produces an addressable campaign-review
artifact.

## Priority 3: connector and capability truth

### Problem

Granola appeared connected but failed at use. Web search was absent from the
connector preflight and failed for missing Tavily configuration. Drive folder
listing existed as a tool but was not advertised by the connection catalog.

### Build

- Derive model tools, connection capabilities, and UI labels from one
  server-owned capability registry.
- Add safe live verification per connector and report `ready`, `degraded`, or
  `unavailable` with a tested-at timestamp.
- Repair Granola list/read error handling and make its failure class useful.
- Configure a supported web-search provider or expose its missing configuration
  before the run starts.
- Fail the workflow preflight when a required capability is unavailable; allow
  the director to approve a degraded run.

### Copy

- OpenWorker: connector descriptors and readiness/recovery model.
- OpenClaw: atomic runtime generations and capability publication.

### Done when

A green connector in Sourcecado succeeds in the corresponding skill run, and a
degraded connector is visible before work begins.

## Priority 4: durable transcript economy and scheduled skill parity

### Problem

Two validation conversations generated thousands of persisted deltas and
multi-megabyte event logs. Scheduling currently stores a raw prompt rather than
a skill plus validated inputs, and historical receipts still use legacy status
`ok`.

### Build

- Coalesce persisted assistant deltas into bounded snapshots/final messages
  while retaining live streaming over WebSocket.
- Add event-log compaction or projection checkpoints without deleting audit
  receipts, tool outcomes, or final artifacts.
- Store `skill_name`, validated inputs, source scope, and grants on a scheduled
  job. Invoke the same skill-run path as chat.
- Normalize scheduler status and artifacts, including historical receipts.
- Park unattended approvals in the existing inbox and resume the same run after
  resolution.

### Copy

- OpenWorker: persisted compaction, automation models, approval inbox, and
  run-once/skip-overlap scheduler.
- Grok Bot: transcript window/index separation and automation spend state.
- OpenClaw: database-first transcript projections and detached-task completion.

### Done when

The outreach skill can be scheduled without a second implementation path, its
approval pauses/resumes durably, and a long artifact run does not grow the
transcript by one durable row per streamed token.

## Proposed implementation sequence

1. Land the narrow Drive/cache truth fixes; avoid merging overlapping PRs
   blindly.
2. Add the skill-run record, phases, checkpoint tool, and forced partial/final
   delivery behavior.
3. Add source-scope and canonical-source contracts.
4. Implement the resumable Drive index for selected folders.
5. Unify capability publication and repair web/Granola preflight.
6. Add the artifact store and campaign-review artifact.
7. Add Google Slides read/copy/update and deck artifact UI.
8. Coalesce transcript persistence and bind scheduler jobs to skills.
9. Re-run both golden workflows interactively.
10. Run outreach through scheduling and continue to enrichment/draft/send only
    after Fisher approves the exact contacts and messages.

## Explicitly deferred

- Generic multi-agent teams and subagent orchestration.
- A broad CRM/Workboard ontology disconnected from the person-file experience.
- Arbitrary local filesystem or shell access as a prerequisite.
- Figma editing before the Google Slides path is proven.
- More connectors that neither golden workflow requires.
- Formal automated eval scoring before several accepted/rejected real examples
  exist.

## Decision gate

Fisher reviews the two local deliverables and this priority order. After that,
the roadmap becomes executable tickets. Until the review, no enrichment,
external draft creation, email sending, or Drive/Slides write is authorized.
