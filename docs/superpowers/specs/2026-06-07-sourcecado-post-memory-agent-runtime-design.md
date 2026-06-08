# Sourcecado Post-Memory Agent Runtime Design

Date: 2026-06-07
Status: Draft for user review

## Summary

Sourcecado should evolve from a permissioned sourcing memory system into a
memory-grounded agent runtime for sourcing work. The next stage should not turn
Sourcecado into a generic CRM, clone The Hog, or jump straight to sandbox
infrastructure. It should add a runtime spine after the current memory roadmap
has a stable, permissioned read foundation.

The recommended direction is a runtime spine:

```text
UI Run / Scheduled Run / API Trigger
  -> Platform Service
      auth, source scope, session, credits later, audit
  -> Agent Service
      executeAgent(agent_id, run_context)
  -> Shared Memory Read Service
      retrieval, temporal filters, citations, gaps, permissions
  -> Tool Registry
      read tools, write tools, admin_only tools
  -> Runtime Adapter
      local process first, worker/sandbox later
  -> Run Ledger
      runs, steps, tool calls, artifacts, logs, metrics
```

The product principle is:

```text
Memory first. Runtime second. Workflow automation third.
```

## Relationship To The Current Roadmap

The current roadmap remains the immediate priority:

1. Real data stress pass.
2. Multi-user source-scoped foundation.
3. Read-only MCP with permission enforcement.
4. Temporal facts and as-of answers.
5. Learned ontology suggestions.
6. Thin Research Chat.

The runtime roadmap begins after the shared read service is trustworthy enough
for agents to use:

7. Agent run ledger.
8. Agent service with `executeAgent()`.
9. Scoped tool registry.
10. Trigger surfaces.
11. Runtime adapter.
12. Multi-agent sourcing workflows.

The dividing line is the shared read service. Sourcecado should not build
autonomous or scheduled agents until memory reads are permissioned, citeable,
and temporal enough to trust.

## Reference Role Of The Hog

The Hog is a reference implementation, not a dependency or host platform.

Patterns worth borrowing:

- context injection from authenticated server state;
- allowlisted tool registration;
- run and tool observability;
- background-job handoffs;
- proposal and review patterns for risky actions;
- CRM and sales tool ideas for later scoped tools.

Patterns not to inherit as core Sourcecado scope:

- broad GTM/CRM product shape;
- sales dashboard ownership;
- full agent tool suite before memory trust is proven;
- external side effects as an early default behavior.

Sourcecado's core should remain a sourcing operating system with durable memory,
not a CRM that happens to have agents.

## Runtime Architecture

The stable center of the runtime is `executeAgent()`.

Every surface should create the same kind of run:

- manual CLI or UI run;
- scheduled run;
- API-triggered run.

Those surfaces should differ in presentation and trigger metadata, not in core
execution semantics. Each run passes through the platform service, resolves
identity and source scope, receives a constrained tool set, executes through the
agent service, and records its work in the run ledger.

The agent service should not own sourcing truth. It consumes the shared memory
read service for retrieval, temporal filtering, citation assembly, gaps, and
permission filtering.

The first runtime adapter can be intentionally simple: local process or worker
execution. CREAO-style sandbox features such as filesystem snapshots,
hot-swappable agent code, S3 FUSE mounts, and isolated runtime state should stay
behind the adapter boundary until real workloads justify them.

## Roadmap Scope

Each runtime checkpoint should prove one platform primitive while still shipping
a useful sourcing capability.

### Checkpoint 1: Agent Run Ledger

Add durable records for agent runs, steps, tool calls, artifacts, errors, and
events.

Purpose:

- make agent work inspectable;
- preserve partial progress after failures;
- create the audit trail needed before scheduling or external actions;
- avoid opaque chat transcripts as the system of record.

### Checkpoint 2: Agent Service

Add a small `executeAgent()` boundary that can run a named sourcing agent with
injected identity, source scope, memory access, and constrained tools.

The first version should support one manual run path before scheduling or API
triggers.

### Checkpoint 3: Scoped Tool Registry

Register tools by capability class:

- `read`;
- `write`;
- `admin_only`.

The registry should inject server-side context and deny unavailable tool classes
before the model sees them.

### Checkpoint 4: Trigger Surfaces

Add trigger surfaces in this order:

1. CLI or manual run.
2. Research Chat or UI run.
3. Scheduled run.
4. API trigger.

All triggers should create the same run shape.

### Checkpoint 5: Runtime Adapter

Keep the first adapter local and simple. Add worker or sandbox execution later
when agents need isolated code, long-running execution, hot-swappable runtime
state, or stronger resource boundaries.

### Checkpoint 6: Multi-Agent Sourcing Workflows

Multi-agent design is intentionally unresolved in this spec.

```text
TODO: Define sourcing-workflow agents after deeper review of the Sourcing
Director workflow.
```

Known direction:

```text
Sourcecado should model the real sourcing operating loop:
find contacts -> research context -> prepare outreach -> manage follow-up
-> capture outcomes into memory
```

The runtime spine should support future named agents safely, but this spec does
not lock in agent names, handoffs, or orchestration.

## Tool And Safety Model

Sourcecado should start with three tool classes.

```text
read
  Can inspect allowed memory and sources.
  Example: ask_memory, search_memory, get_source, list_gaps.

write
  Can write low-risk internal Sourcecado state.
  Example: remember_note, mark_gap_reviewed, save_run_artifact,
  create_draft_artifact.

admin_only
  Can perform privileged operations or create user-facing proposals that
  require review.
  Example: ingest, refresh, change_permissions, approve_ontology_change,
  propose_contact_update, draft_followup_for_review.
```

The enforcement model:

```text
caller identity + source scope + run type
  -> allowed tool classes: read / write / admin_only
  -> registered tools
  -> server-injected context
  -> audited tool execution
```

The important simplification is that proposal-required tools are folded into
`admin_only`. Anything that could influence external workflows, user-facing
records, ontology, permissions, or future team behavior should be available only
in trusted/admin-created run contexts.

A normal scheduled sourcing run can read memory and write internal artifacts. It
cannot create official contact-change proposals, draft official follow-ups,
approve ontology changes, ingest sources, or mutate permissions unless the run
was started in an admin context.

## Run Data Flow

Every run follows one common flow:

```text
trigger request
  -> authenticate caller
  -> resolve source scope
  -> create agent_run
  -> compile run context
  -> register allowed tools
  -> execute agent through runtime adapter
  -> record steps, tool calls, logs, artifacts, and errors
  -> return result to caller
```

The run ledger is the system of record.

Candidate records:

```text
agent_runs
  id, trigger_type, caller_type, caller_id, source_scope, status, started_at,
  completed_at

agent_steps
  run_id, step_index, agent_id, input_summary, output_summary, status

tool_calls
  run_id, step_id, tool_name, tool_class, input_summary, output_summary, status,
  error

run_artifacts
  run_id, artifact_type, title, content_json, source_refs, created_by_step

run_events
  run_id, event_type, message, metadata_json, created_at
```

The first implementation can keep this compact, but agents should leave behind
auditable work products that Sourcecado can inspect, replay, summarize, and
eventually ingest into memory.

## Error Handling

Runtime errors should be explicit and recorded.

```text
auth/source-scope failures
  -> deny before retrieval or tool registration

tool validation failures
  -> record failed tool_call with safe error summary

agent/runtime failure
  -> mark agent_run failed, preserve completed steps/artifacts

scheduled/API trigger failure
  -> record run_event, expose retry status later

memory lookup gaps
  -> return as structured gaps, not as runtime errors
```

Errors should not erase completed work. A failed run should still show what
steps completed, what tool calls were attempted, which artifacts were produced,
and which boundary failed.

## Verification

Verification should prove platform boundaries, not just answer quality.

Core tests:

- permission tests: the same task with different source scopes returns
  different allowed evidence;
- tool registry tests: read-only runs cannot register write or admin_only tools;
- run ledger tests: every run records status, steps, tool calls, artifacts, and
  errors;
- trigger parity tests: CLI, UI, API, and scheduled triggers all create the
  same run shape;
- memory boundary tests: agent answers and tool calls use the shared read
  service, not duplicate retrieval logic.

For the first implementation plan, the acceptance bar should be:

- one named agent;
- one manual trigger;
- a minimal run ledger;
- read/write/admin_only tool classes;
- proof that unauthorized tools and sources cannot leak into the run.

## Out Of Scope

The runtime stage should not include:

- full CRM dashboards;
- autonomous outreach sending;
- broad sales tool suite;
- billing as a core primitive;
- sandbox snapshots or hot-swap implementation;
- final multi-agent sourcing workflow design.

Those can become later layers once the run system proves itself.

## Open Decisions For The Implementation Plan

- Which manual trigger should be first: CLI run or Research Chat/UI run?
- What is the first named agent?
- Which storage layer should hold the run ledger in the first implementation:
  the existing local SQLite database or a hosted Postgres path?
- Which initial read and write tools are enough to prove the registry boundary?
- How should admin-created run contexts be represented before a full admin UI
  exists?
