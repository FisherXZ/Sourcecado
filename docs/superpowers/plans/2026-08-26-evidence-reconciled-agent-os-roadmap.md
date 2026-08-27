# Evidence-Reconciled Agent OS Roadmap

Date: 2026-08-26
Status: approved architecture basis
Evidence: `docs/qa/2026-08-26-golden-workflow-baseline.md`
Product source: `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md`

## Product conclusion

Sourcecado is a generic local agent operating system for the club. Sourcing is
the first department whose work must be proven end to end. Skills improve how
the agent approaches a job, but durability, tools, artifacts, connectors,
scheduling, and audit history exist independently of skills.

The two live sourcing workflows exposed five missing operating-system
capabilities. Each priority below names one primary implementation donor and a
minimal Sourcecado port. We do not combine frameworks merely because all three
contain adjacent ideas.

## Core execution model

- A director gives Sourcecado a job as a prompt. The prompt may be freeform,
  may load one or more skills, or may come from an automation.
- One Agent Run owns that job through clarification questions, approval waits,
  tools, interruption, resume, and terminal delivery.
- Chat and scheduling invoke the same agent engine.
- Files and cloud documents are deliverables. Assistant prose alone is not a
  substitute when the job calls for an editable artifact.
- The Run Ledger preserves the complete semantic agent trace for future evals,
  without storing hidden chain-of-thought or transport-level token chunks.

## Priority 0: Durable Agent Run

### Observed failure

Both golden workflows found useful evidence but kept researching until the
operator interrupted them. Each required a second user message saying, in
effect, "stop using tools and deliver now."

### Primary donor

**OpenWorker's prompt-driven `TurnEngine` lifecycle.**

Copy these concrete behaviors:

- a prompt enters one engine regardless of whether a skill is loaded;
- the run can suspend for a human question or approval and continue in place;
- interruption preserves the visible partial assistant output;
- unanswered tool calls resume durably after restart;
- completed model/tool iterations are persistence checkpoints;
- context compaction preserves canonical history while bounding the outbound
  model view;
- automation sends ordinary instructions through the same engine.

Use OpenClaw only as a contract check for the distinction between Session,
Agent Run, model turn/attempt, approval ownership, and terminal state.

### Sourcecado port

- Preserve Sourcecado's provider adapters, tool execution, approval inbox,
  semantic events, and replay UI.
- Add a durable Agent Run around the existing loop rather than replacing the
  loop wholesale.
- A run stores stable identity, owning session, trigger, original goal, current
  state, checkpoints, source and artifact refs, usage, and terminal result.
- Clarification uses an `ask_user` suspension inside the same run. A later
  request to revise a completed deliverable becomes a new linked run.
- Skills are optional context recorded as `skills_loaded`; they are never the
  execution boundary.
- Reserve enough budget for terminal delivery. When the work budget ends, the
  engine returns the best useful partial result with explicit gaps instead of
  starting another tool round.
- Scheduled jobs call this same Agent Run entry point with their saved prompt.

### Do not copy

- OpenWorker's full connector catalog or generic UI.
- OpenClaw's Gateway/runtime breadth.
- Skill-specific state machines or hard-coded sourcing phases.

### Done when

The outreach and pitch jobs reach their director-review deliverables in one
Agent Run, including any clarification/approval waits, without a follow-up
"converge now" prompt.

## Priority 1: Scoped Knowledge Workspace

### Observed failure

The outreach run surfaced 33 Drive matches and the pitch run 155. Duplicate,
stale, and unrelated sources entered context. Large documents truncated before
relevant sections.

### Product shape

The director selects the Codeology Sourcing Drive folder once. Google Drive
remains authoritative. Sourcecado maintains a local read-only mirror/index of
that folder with Drive id, folder path, MIME type, modified time, sensitivity,
and extracted text.

### Primary donor

**OpenClaw's Markdown memory index in `packages/memory-host-sdk`.**

Copy only this coherent path:

- one explicit indexed path;
- symlink-safe file discovery and real-path deduplication;
- hash, modification-time, and size change detection;
- Markdown chunks with line boundaries;
- SQLite source/chunk tables and FTS search;
- incremental resync when files change;
- bounded snippets with file/line provenance.

### Sourcecado port

- Mirror supported Drive documents into Sourcecado's local state; never treat
  the mirror as the authoritative document store.
- Preserve Drive metadata beside every mirrored file/chunk.
- Normal agent retrieval uses `search_sources` and `read_source` against this
  one Knowledge Workspace.
- Global Drive search remains an explicit scope-expansion action, not the
  default retrieval path.
- A human may pin approved SOPs/templates later, but v1 does not ask the model
  to infer canonical status from filenames or dates.

### Do not copy

- Vector embeddings, MMR, temporal decay, multimodal memory, session transcript
  indexing, project annotations, or automatic canonical inference.
- Grok Bot's separate transcript index worker.
- OpenWorker's entire filesystem tool suite as part of retrieval.

### Done when

After selecting the Fall 2026 sourcing folder, a Zoox query returns fewer than
ten relevant scoped results with Drive/source citations and no unrelated global
Drive matches.

## Priority 2: Workspace, Shell, and Artifacts

### Observed failure

The pitch workflow produced strong prose but no editable deck. Neither workflow
produced a durable file artifact. Treating PPTX, DOCX, XLSX, PDF, HTML, and CSV
as separate platform features would create the wrong abstraction.

### Primary donor

**OpenWorker's Cowork workspace and artifact lifecycle.**

Copy these concrete behaviors:

- one per-run writable scratch workspace;
- workspace-scoped file read/write and search;
- a persistent, workspace-rooted, approval-gated shell;
- `todo_write` as visible execution progress;
- the instruction to finish with the actual file plus an `artifact:` link;
- artifact discovery by scanning the run's scratch workspace;
- preview/read/open/reveal behavior for common file types;
- scheduled-run artifact collection from files modified during the run.

### Sourcecado port

- Use files in the run workspace as the initial artifact source of truth. The
  Run Ledger records references and provenance; do not add an artifact database
  before file-backed artifacts prove insufficient.
- Add skills that teach the agent how to create and verify common formats using
  the shell: PPTX, DOCX, XLSX, PDF, HTML, Markdown, CSV, images, and packages.
- Skills specify the appropriate library/tool, render the output, inspect the
  rendering, and repair layout/content defects before delivery.
- Cloud-native Google Slides, Docs, and Sheets remain connector tools. They are
  not special cases in the artifact runtime.
- Google Slides is the first cloud artifact adapter required by the pitch
  workflow; Google Docs and Sheets follow the same connector boundary.

### Do not copy

- OpenClaw's box/remote-computer infrastructure.
- Grok Bot's canvas/attachment internals.
- One bespoke runtime subsystem per office format.
- A restricted artifact-only command language that would grow into a shell.

### Done when

The pitch job creates a real editable PowerPoint artifact in its workspace and,
when requested, an editable Google Slides copy through the Google connector.
The outreach job creates a reviewable campaign package as a file artifact.

## Priority 3: Connector Truth

### Observed failure

Granola appeared connected but failed on its first real read. Web search failed
for missing configuration despite no visible preflight warning. Drive folder
listing existed as a tool but was absent from the connection catalog.

### Primary donor

**OpenWorker's `ConnectorDescriptor` and `ValidationResult` model.**

Copy these concrete behaviors:

- each connector owns its authentication shape, scopes, setup copy, safe
  identity, pinned capabilities, and real validator;
- validation makes a bounded provider call instead of checking only for a
  stored credential;
- the same descriptor drives setup, status, and tool publication;
- connected account, persona default, and session override are separate
  concerns.

### Sourcecado port

- Create descriptors only for Sourcecado's small connector set.
- Generate `/v1/connectors`, model tools, and UI capability labels from the same
  descriptor data.
- "Connected" means the provider validator passed. Store safe identity,
  `last_verified_at`, status, and a recovery message.
- Validate on connect and before a run that requires the connector.
- Granola validation performs the MCP handshake and a harmless bounded read.
- Web search becomes a visible connector with configuration/validation state.
- Failed validation permits a degraded run by default. Block only when the
  missing capability makes the requested deliverable impossible.

### Do not copy

- OpenWorker's 25-connector catalog, cloud OAuth broker, messaging gateway, or
  persona connector-management breadth.
- OpenClaw runtime generations as a second connector system.

### Done when

A green connector succeeds in the corresponding golden workflow. A degraded
connector is visible before work begins, and the director can choose whether to
continue when the final deliverable remains possible.

## Priority 4: Semantic Agent Trace

### Observed failure

The two validation conversations persisted roughly 1,900 and 3,300 assistant
delta events, producing event logs up to 1.2 MB. Sourcecado needs a complete
trace for later evals, but transport-level token chunks are not the trace.

### Primary donor

**OpenWorker's canonical `engine.messages` plus semantic checkpoint persistence.**

Copy these concrete behaviors:

- stream text/reasoning deltas live to connected clients;
- keep canonical completed messages and tool results in session history;
- persist at meaningful checkpoints: user input, waiting approval/question,
  completed model/tool iteration, and terminal completion/interruption;
- always perform a final save in cleanup;
- compact only the outbound model view while preserving canonical history.

### Sourcecado port

- Keep live WebSocket streaming.
- Coalesce assistant deltas into a completed model-message record for durable
  storage.
- Preserve the full semantic trace: original goal and user answers,
  prompt/context version or hash, model/provider/configuration, completed model
  turns, tool arguments/results, approvals, sources, checkpoints, timing,
  usage/cost, artifacts, errors, terminal result, and later human feedback.
- Keep visible reasoning and structured rationale summaries when present.
- Never store hidden chain-of-thought.
- Scheduling already calls the same Sourcecado engine; do not create a second
  scheduling runtime or require a skill name on a job.
- Treat legacy scheduler status normalization as a small bug, not an
  architecture priority.

### Do not copy

- OpenClaw's full database-first transcript projection system.
- Grok Bot's transcript mirror.
- Durable per-token stream chunks or hidden model reasoning.
- A separate skill-aware automation runtime.

### Done when

Reload shows final messages, meaningful progress/tool/approval receipts, and
terminal state. The trace can be exported for evals without replaying thousands
of token-delta rows.

## Implementation sequence

1. Reconcile and land the narrow Drive/cache truth fixes; do not merge
   overlapping branches blindly.
2. Port the OpenWorker Durable Agent Run lifecycle around Sourcecado's existing
   loop.
3. Add the selected Drive Knowledge Workspace and narrow OpenClaw FTS index.
4. Port OpenWorker's per-run workspace, files/search/shell/todo, and artifact
   surface.
5. Create and verify common artifact-format skills; add Google Slides as the
   first cloud artifact connector.
6. Replace the hard-coded connector catalog with OpenWorker-style descriptors
   and real validators; repair Granola/web readiness.
7. Change delta persistence to the OpenWorker checkpointed semantic trace.
8. Re-run both golden workflows interactively.
9. Run an ordinary saved prompt through scheduling and verify the same Agent
   Run, connector, artifact, approval, and trace paths.
10. Continue to enrichment, draft creation, and sending only after Fisher
    approves the exact contacts and messages.

## Existing work disposition

- PR #49's narrow Drive MIME/cache work supports Priority 1 and should be
  reconciled before new retrieval work.
- Issue #43 is directly supported by the Knowledge Workspace evidence, but its
  implementation should follow the approved narrow local-index design.
- Issue #41 now aligns with Priority 2 when implemented as the OpenWorker-style
  workspace/files/shell boundary.
- PR #47's broad generic Board/index ontology is not part of this architecture
  basis and should not be merged wholesale without product-model review.
- Issue #40 remains a small scheduler receipt migration/normalization fix.

## Explicitly deferred

- Generic multi-agent teams and subagent orchestration.
- A broad CRM/Workboard ontology disconnected from person files.
- Hosted/team tenancy.
- Automatic sending or bulk enrichment.
- Vector retrieval in the first Knowledge Workspace index.
- Figma editing before local PPTX and Google Slides are proven.
- More connectors that neither golden workflow requires.
- Formal automated scoring before accepted/rejected real examples exist.

## Acceptance

The architecture is successful when both golden jobs finish in one Agent Run,
use the scoped Knowledge Workspace, produce real editable artifacts, degrade
truthfully when a connector fails, survive interruption/restart, work through
scheduling, and leave a complete eval-ready semantic trace.
