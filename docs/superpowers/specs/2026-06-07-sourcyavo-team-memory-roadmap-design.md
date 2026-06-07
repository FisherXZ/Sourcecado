# SourcyAvo Team Memory Roadmap Design

Date: 2026-06-07
Status: Draft for user review

## Summary

SourcyAvo should grow from its current local CLI into a small, permissioned
team sourcing memory system. The roadmap should stay grounded: each checkpoint
must ship a useful Codeology sourcing workflow and prove one serious memory
system primitive.

The next phase is not a generic company brain and not a full GBrain or Graphiti
clone. It is a Codeology sourcing memory system that borrows proven patterns
where they solve current product needs:

- GBrain-style multi-user source scoping and MCP surfaces.
- Graphiti-style temporal fact validity and invalidation.
- Reviewed ontology evolution from messy real source material.

## Current Baseline

SourcyAvo currently has a local TypeScript CLI that can:

- ingest exported `.md`, `.txt`, `.csv`, and `.eml` files;
- create source records and memory chunks;
- refresh structured entities, relationships, and semantic facts;
- cache extraction runs;
- answer questions with `Answer`, `Evidence`, `Gaps`, and `Next Action`;
- surface candidate, conflicted, and stale memory as gaps.

The current MVP is single-user local memory. That boundary no longer matches
the intended use case because SourcyAvo will be shared by a team with different
permission scopes.

## Product Direction

SourcyAvo should optimize for a hybrid goal:

- teach modern memory-system architecture through real implementation, and
- help Codeology Sourcing Directors answer real sourcing-history questions.

The roadmap should avoid a vague grand vision. The product should move through
grounded checkpoints that can be tested against real Codeology data and real
sourcing questions.

## Roadmap Checkpoints

### Checkpoint 1: Real Data Stress Pass

Run broad real Codeology source material through the existing local pipeline.
This should include available sourcing Sheets, Notion/Drive exports, and email
exports when safe to use.

Purpose:

- expose real ingestion, extraction, citation, retrieval, and ontology failures;
- replace fixture-only confidence with actual sourcing-memory evidence;
- define the true requirements for team access, MCP tools, temporal answers,
  and Research Chat.

Success criteria:

- real exports ingest without one bad file stopping the run;
- ingestion and extraction failures are recorded with actionable reasons;
- benchmark sourcing questions can be run and misses can be classified as
  extraction, retrieval, missing-source, temporal, citation, or permission
  failures.

### Checkpoint 2: Multi-User Source-Scoped Foundation

Add the team-sharing foundation before exposing the system broadly.

Core behavior:

- each source/import has a stable `source_id`;
- source-backed rows carry source identity;
- users, sessions, and OAuth-style clients have explicit allowed source scopes;
- scoped clients can represent agent/MCP callers without pretending every agent
  is a full human user;
- reads and writes are filtered through central source-scope helpers;
- restricted source material is filtered before retrieval and answer synthesis.

This borrows GBrain's important idea without importing all of GBrain's
operational scale: scope must be enforced structurally, not by prompt wording.

Success criteria:

- at least two test users or clients can have different source access;
- OAuth-style client identity can be mapped to an allowed source scope;
- the same query returns different allowed evidence for different scopes;
- restricted material does not appear in search results, source lookups,
  citations, gaps, or final answers for unauthorized callers.

### Checkpoint 3: Read-Only MCP With Permission Enforcement

Expose SourcyAvo through MCP so Codex, Claude Code, or a similar agent can use
the memory layer directly.

Initial MCP tools should be read-only:

- `ask`: ask a sourcing-memory question;
- `search_memory`: retrieve relevant allowed memory;
- `get_source`: inspect an allowed source/citation;
- `list_gaps`: inspect missing, candidate, conflicted, stale, superseded, or
  invalidated memory.

Admin and mutation operations remain separate:

- `ingest` stays CLI/admin-only;
- `refresh` stays CLI/admin-only;
- permission changes stay admin-only.

Success criteria:

- a scoped MCP caller can answer real sourcing questions;
- MCP results are structured enough for an agent to use, not just terminal
  prose;
- every MCP read respects the caller's permission scope;
- unauthorized callers cannot see restricted evidence or trigger admin actions.

### Checkpoint 4: Temporal Facts And As-Of Answers

Upgrade facts from flat statuses into a lightweight temporal model inspired by
Graphiti.

The current statuses are still useful as answer-facing projections:

- `candidate`;
- `accepted`;
- `conflicted`;
- `stale`;
- `superseded`;
- `invalidated`.

But the underlying model should add temporal fields such as:

- `observed_at`: when SourcyAvo learned or extracted the fact;
- `valid_at`: when the fact became true in the world, if known;
- `invalid_at`: when the fact stopped being true, if known;
- `superseded_by_fact_id`: the newer fact that replaced this one, if any;
- source provenance back to the source record/chunk.

The key behavior is invalidation instead of deletion. If a contact was marked
as needing follow-up in March and later marked as responded in April, the March
fact should remain available for historical answers.

Success criteria:

- "Who needs follow-up now?" and "Who needed follow-up in March?" can return
  different, explainable answers;
- old facts remain citeable;
- contradictory or superseding facts are surfaced as gaps or temporal notes
  rather than silently overwritten;
- temporal behavior is tested with fixtures and real-data snapshots.

### Checkpoint 5: Learned Ontology Suggestions

Add ontology learning as a reviewed suggestion workflow, not as automatic schema
mutation.

SourcyAvo starts with a prescribed sourcing ontology:

- Contact;
- Organization;
- Outreach Attempt;
- Outreach Outcome;
- Domain;
- Event;
- Semester;
- Source Material.

During refresh, the system should detect recurring patterns that do not fit the
current ontology, such as:

- repeated unknown relationship names;
- recurring source fields that are not mapped;
- ambiguous outreach statuses;
- entity types that repeatedly appear in real data;
- facts that cannot be classified cleanly.

Those become ontology suggestions. An admin can accept, rename, merge, or
reject them. Only accepted ontology changes affect future extraction and
answers.

Success criteria:

- refresh can produce ontology suggestions from real data;
- suggestions include examples and source citations;
- rejected suggestions do not affect extraction;
- accepted suggestions become explicit, testable schema/extraction behavior.

### Checkpoint 6: Thin Research Chat

Build a simple web Research Chat for Sourcing Directors after the permissioned
read layer and MCP surface are working.

The UI should stay narrow:

- question input;
- four-section sourcing answer;
- inspectable source citations;
- visible gaps and temporal notes;
- no CRM dashboard;
- no automatic outreach.

Research Chat must reuse the same shared read service as MCP. It should differ
in presentation, not in retrieval semantics, permission filtering, or answer
logic.

Success criteria:

- a Sourcing Director can answer the benchmark sourcing questions without using
  CLI or MCP directly;
- the UI never shows unauthorized source material;
- citations and gaps are inspectable enough for manual verification.

## Shared Architecture

SourcyAvo should have one memory layer and multiple surfaces.

```text
Real Codeology Source Material
  -> Source Records
  -> Memory Chunks
  -> Entities / Relationships
  -> Temporal Semantic Facts
  -> Shared Read Service
      -> CLI ask
      -> MCP read tools
      -> Research Chat
```

The shared read service is the important boundary. It owns:

- permission/source scope;
- temporal filters;
- retrieval;
- citation assembly;
- gap formatting;
- answer contract.

CLI, MCP, and Research Chat should not each implement their own retrieval or
truth rules.

## Identity And Permission Direction

Team sharing is part of the product, not a later enterprise add-on. The first
multi-user checkpoint should model identity explicitly enough to prove that
SourcyAvo can be safely shared by multiple Codeology users and agent clients.

Minimum model:

- `users`: human teammates;
- `oauth_clients`: scoped agent or app clients;
- `sources`: imported source collections or files;
- `source_permissions`: user/client access to allowed sources;
- `audit_events`: reads, denied reads, permission changes, and admin actions.

The exact auth provider is an implementation-plan decision. The architectural
decision is already made: SourcyAvo needs login/client identity and source
scope before retrieval, not after answer generation.

## Temporal Fact Direction

Temporal behavior should be lightweight but real. A fact should not disappear
just because a later refresh found a better or newer version.

Candidate fields:

- `observed_at`: when SourcyAvo learned the fact;
- `valid_at`: when the fact appears to have become true in the world;
- `invalid_at`: when the fact appears to have stopped being true;
- `expired_at`: when SourcyAvo stopped treating the fact as currently active;
- `superseded_by_fact_id`: pointer to the newer fact;
- `source_record_id` and `memory_chunk_id`: provenance.

Answer logic can still present simple statuses, but storage should preserve
enough history to answer "what did we believe then?" and "what appears true
now?" without inventing facts.

## Learned Ontology Direction

Learned ontology machinery should help SourcyAvo adapt to real Codeology data
without letting extraction drift mutate the system behind the team's back.

Suggested flow:

1. Refresh notices repeated unknown fields, labels, relationships, entity
   shapes, or status phrases.
2. SourcyAvo stores an ontology suggestion with examples, citations, frequency,
   and proposed mapping.
3. An admin accepts, renames, merges, or rejects the suggestion.
4. Accepted changes become explicit extraction configuration and test cases.

This gives the project the useful part of a learned ontology system: the memory
layer can notice that its vocabulary is too small. It avoids the dangerous
part: silently changing the schema or answer semantics without review.

## Borrowed Patterns

### Borrow From GBrain Now

- Multi-user hosted access.
- OAuth-scoped clients for agent and app access.
- Source-scoped reads and writes.
- MCP as an agent-native read surface.
- Separation between user read tools and admin/local operations.
- Auditability around requests and source access.

### Borrow From GBrain Later

- Large durable job queue.
- Full dream-cycle automation.
- Production-scale company-brain operating model.

These should be added only when real refresh/runtime needs justify them.

### Borrow From Graphiti Now

- Temporal fact validity windows.
- Fact invalidation and supersession instead of deletion.
- "As of date X" answers.
- Provenance from facts back to source material.

### Borrow From Graphiti Carefully

- Learned ontology machinery should enter as reviewed suggestions, not automatic
  schema mutation.
- Graph traversal should stay sourcing-specific at first.

### Do Not Clone Yet

- Full Python graph database engine.
- Fully learned ontology without review.
- Arbitrary graph traversal as the primary answer mechanism.
- Per-row permission complexity beyond source/fact scope unless real data
  proves it necessary.

## Graph Traversal Scope

Graph traversal is useful, but the first version should be constrained to
sourcing workflows:

- Contact -> Organization;
- Contact -> Outreach Attempt -> Outreach Outcome;
- Contact -> Domain;
- Contact -> Semester/Event;
- Contact -> Past Collaborator.

The system should avoid broad arbitrary traversal until real questions require
it. This keeps answers explainable and testable.

## Out Of Scope

- Autonomous outreach.
- Sending email or messages.
- Full CRM workflows.
- Messenger integration unless privacy/access is explicitly solved.
- Full GBrain minions/job architecture.
- Full Graphiti graph engine.
- Learned ontology that mutates accepted schema without review.
- Generic company-brain product scope outside Codeology sourcing.

## Verification Philosophy

Every checkpoint should be tested against both controlled fixtures and real
Codeology source snapshots.

The most important verification rules:

- permission checks must cover search, answers, citations, gaps, and source
  lookup;
- temporal tests must prove different answers at different dates;
- ontology suggestions must be reviewable and reversible;
- real-data failures should be turned into named categories rather than buried
  in logs.

## Benchmark Questions

The roadmap should continue using the original sourcing benchmark questions,
expanded with permission and temporal variants:

- Who did we contact last semester for AI, biotech, startups, or design?
- Who responded?
- Who did not respond?
- Who needs follow-up now?
- Who needed follow-up during a specific month or semester?
- Which companies or people worked with Codeology before?
- What outreach style worked?
- What did not work?
- What facts are conflicted, stale, superseded, or missing?
- What sources was this answer allowed to use?

## Open Decisions For The Implementation Plan

- Which concrete auth provider or local development auth shim should implement
  the first OAuth-style user/client boundary?
- What source taxonomy should be used for the first real Codeology data import?
- Which real-data snapshot becomes the repeatable test corpus?
- How should ontology suggestions be represented before there is an admin UI?
- Whether MCP should be stdio-only first or support HTTP later.
