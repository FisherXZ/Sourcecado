# Sourcecado Mature Agent System Blueprint

Date: 2026-08-26
Status: approved architecture basis

## Product boundary

Sourcecado is the local agent operating system for Codeology. Sourcing is its
first proven department. Sourcecado owns the product experience, sourcing
domain, person files, local runtime, and club memory. Reference frameworks are
implementation donors, not product dependencies.

## Architecture rule

For every major subsystem:

1. Start from an observed product failure.
2. Choose one primary reference implementation.
3. Name the exact behavior/code boundary being copied.
4. Adapt only what Sourcecado's local architecture or domain requires.
5. State what is deliberately not being copied.

Do not create abstract "best-of-three" hybrids. When none of the three systems
implements a product-specific connector such as Google Slides, ground that
connector in the official vendor API and keep it behind a Sourcecado-owned
tool boundary.

## Core concepts

### Prompt-driven execution

Every Agent Run begins from instructions. The instructions may be a freeform
prompt, may load skills, or may come from scheduling. Skills are optional
resources; they do not own execution.

### Goal-sized Agent Run

One Agent Run owns one user job through clarification, approvals, tools,
interruption, resume, and terminal delivery. A later revision to a completed
deliverable is a new linked run.

### Knowledge Workspace

The director selects one Google Drive sourcing folder. Drive remains the source
of truth; Sourcecado maintains a local read-only searchable projection.

### Artifact Workspace

Every run receives a writable scratch workspace. The agent uses files, search,
shell, and skills to create actual deliverables. The workspace's files are the
initial artifact source of truth.

### Connector truth

Connector availability is proven by a real validator and published from the
same descriptor that owns its tools. Stored credentials alone do not mean the
capability is ready.

### Semantic Agent Trace

The Run Ledger preserves all observable decisions, inputs, model turns, tools,
sources, approvals, artifacts, outcomes, timing, usage, errors, and feedback
needed for audit and eval. Live token chunks and hidden chain-of-thought are not
durable records.

## Primary implementation donors

| Priority | Primary donor | Coherent subsystem copied |
|---|---|---|
| Durable Agent Run | OpenWorker | Prompt-driven TurnEngine lifecycle, ask/approval suspension, partial preservation, durable resume, iteration checkpoints, compaction, same-engine automation |
| Knowledge Workspace | OpenClaw | Narrow Markdown memory-host index: explicit path, hash/mtime/size, chunks, SQLite FTS, incremental sync, path/line provenance |
| Workspace and Artifacts | OpenWorker | Cowork scratch workspace, files/search/persistent shell/todo, artifact links, discovery, preview/open, scheduled artifact collection |
| Connector Truth | OpenWorker | ConnectorDescriptor, real validate call, safe identity, pinned capabilities, status/tool publication |
| Semantic Agent Trace | OpenWorker | Canonical messages plus semantic checkpoint persistence, live ephemeral deltas, final cleanup save |

OpenClaw remains the contract reference for Session/Run/turn/approval ownership
and eval-ready trajectories. Grok Bot remains useful prior art for later
checkpoint or index resilience, but no approved core priority currently requires
copying its subsystem.

## Invariants

1. A prompt works whether or not it names or loads a skill.
2. Chat and scheduling invoke the same Agent Run entry point.
3. A run can wait for a question or approval without becoming a second run.
4. Restart never discards completed tools, partial visible output, or artifacts.
5. Expensive or external actions execute at most once per approved attempt.
6. Normal sourcing retrieval stays inside the selected Knowledge Workspace.
7. Google Drive remains authoritative over its local mirror.
8. A requested file deliverable is not complete until a real editable file or
   cloud document exists and has a receipt.
9. Local formats are created through workspace tools plus skills; Google-native
   formats use connector tools.
10. A green connector has passed a real provider validator.
11. A degraded run may continue when the requested outcome remains possible.
12. Live streaming is ephemeral; the semantic execution trace is durable.
13. Eval data excludes credentials, hidden chain-of-thought, and unrelated
    private source bodies.

## Golden workflow proof

### Outreach campaign

One prompt should produce a scoped shortlist, approved enrichments and sends,
an editable campaign package, person/sequence updates, and next actions. It must
survive clarification, approval waits, restart, and scheduled execution.

### Company pitch package

One prompt should use the scoped Knowledge Workspace, produce a company brief,
create an editable PPTX and optional Google Slides copy, preserve source refs,
and stop at a director review gate without claiming unsupported work.

## Explicit boundaries

- Keep the current FastAPI sidecar and React/Tauri product.
- Keep Sourcecado's provider, tool, approval, person-file, and UI work that
  already passes real product tests.
- Do not replace the runtime wholesale with OpenWorker or OpenClaw.
- Do not make skills, MCP, OpenClaw, or OpenWorker core dependencies.
- Do not add generic platform breadth without evidence from a real club job.
