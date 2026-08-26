# Sourcecado Mature Agent System Blueprint

Date: 2026-08-26  
Status: working reference architecture  
Purpose: define the mature system we are testing toward before local workflow
failures bias the architecture toward one-off fixes.

## Product boundary

Sourcecado owns the Sourcing Director experience, sourcing domain, person
files, institutional memory, and opinionated skills. OpenClaw, Grok Bot, and
OpenWorker are implementation references. They are not product dependencies.

## Reference roles

| Reference | Strongest patterns to copy | Do not copy |
|---|---|---|
| OpenClaw | durable run ownership, session recovery, bounded context and compaction, approval ownership and atomic allow-once use, detached tasks, execution attempts and proof | multi-channel Gateway breadth, generic plugin platform, large Workboard ontology |
| Grok Bot | desktop lifecycle, per-agent transcript state, turn checkpoints, transcript/index workers, request deduplication, interruption and resumption, automation spend state | agent roster, remote box/cloud infrastructure, reconstructed renderer internals |
| OpenWorker | loopback sidecar, launch-token boundary, connector lifecycle, skill loading, personas, permission decisions, approval inbox, run-once catch-up and skip-on-overlap scheduler | 25-connector breadth, generic coworker product surface, cloud OAuth broker |

## Target subsystem decisions

| Subsystem | Sourcecado decision | Current position |
|---|---|---|
| Runtime owner | Keep the FastAPI sidecar as the single local brain. Copy lifecycle invariants, not another framework. | Built |
| Run identity | Every interactive, scheduled, or background execution has a stable run id, owner session, status, timestamps, and terminal receipt. | Partially built |
| Checkpoints | A multi-step run can persist progress after meaningful work and resume without replaying external writes. Use idempotency keys at write boundaries. | Gap to prove |
| Event history | Keep the versioned append-only event spine. Treat UI transcripts as projections of durable events. | Built, must product-test |
| Context | Bound prompt size. Carry durable facts and artifact references forward instead of replaying an unbounded transcript. | Partial |
| Tool registry | Sourcecado owns named tools, schemas, risk class, target scope, and result contract. The model never self-grants capability. | Built |
| Approvals | Approval records are durable, bound to tool/run/target, resolve once, expire safely, and create an immutable receipt. | Built, must product-test |
| Connectors | One safe status/capability contract feeds model, runtime, and UI. Tokens remain outside prompts and events. | Partial |
| Skills | Skills are discoverable, loaded on demand, and usable through the same path in chat and scheduling. | Loader built; real skills unproven |
| Artifacts | Deliverables are first-class, addressable, editable/versioned when appropriate, source-linked, and visible after restart. | Partial |
| Memory | Store accepted sourcing facts, outcomes, corrections, gaps, and handoff context with provenance. Do not treat the transcript as memory. | Partial |
| Background work | Long or fan-out jobs run outside the chat step budget with progress, cancellation, checkpoint, and resume. | Gap to prove |
| Scheduling | Scheduling invokes the same skill/run path as chat, with unattended approvals parked in the inbox. | Skeleton built |
| Operator control | The director can see progress, stop work, resolve approvals, inspect evidence, recover failures, and open final artifacts. | Broadly built |
| Evaluation | Begin with preserved run evidence and Fisher's accept/reject feedback. Add formal evals after repeated real examples exist. | Manual now |

## Mature-system invariants

1. The same skill can run interactively or from a schedule.
2. Every run has one durable identity and one canonical terminal status.
3. A restart never silently loses successful completed work.
4. An external write is executed at most once unless the user explicitly retries it.
5. Approval is attached to the exact action and target, not a vague tool class.
6. Source scope is explicit; a run cannot compensate for a scoped-source failure
   by silently searching unrelated material.
7. Tool results, sources, artifacts, and decisions have stable references.
8. Model context is bounded; durable state is not reconstructed from an
   indefinitely growing transcript.
9. Scheduled work uses the same permission, ledger, artifact, and memory path
   as an interactive run.
10. The UI never reports success without a durable receipt.
11. The agent never claims a capability absent from its active tool registry.
12. A completed workflow leaves the club in a better future state: people,
    sources, outcomes, artifacts, and next actions remain usable later.

## How the golden skills exercise the blueprint

| Capability | Outreach campaign | Company pitch package |
|---|---|---|
| Run shape | fan-out across many people | deep synthesis into one artifact set |
| Expensive actions | Apollo enrichment, sending | model/tool time, possible slide write |
| Approvals | per enrichment, draft review, send | template choice, final artifact approval |
| Sources | Apollo, Gmail, memory, Drive SOPs, web | Drive SOPs/templates, Gmail, memory, web, meeting notes |
| Checkpoints | per candidate and per message | research, outline, deck version, final package |
| Artifacts | shortlist, drafts, send receipts, follow-up list | brief, editable deck, talking points, follow-up draft |
| Memory | person and campaign outcomes | company relationship, ask, decisions, gaps |
| Scheduled mode | campaign refresh/follow-up review | optional research refresh before a meeting |

## Copy rule during implementation

When a product run exposes a system gap, first locate the corresponding
OpenClaw, Grok Bot, and OpenWorker implementation. Record which contract and
failure behavior we are copying. Adapt it only where Sourcecado's sourcing
domain or local architecture requires a different interface.
