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

## Eng Review Scope Decision

The next implementation plan should not attempt all six roadmap checkpoints in
one branch. That would turn a focused memory-system hardening pass into a broad
rewrite across ingestion, permissions, MCP, temporal facts, ontology review, and
web UI.

Approved next scope:

1. Checkpoint 1: real-data stress pass.
2. The smallest useful slice of Checkpoint 2: source-scoped read enforcement.

Everything else remains roadmap direction until the real-data and permission
primitive is proven.

The next branch should ship:

- real Codeology export ingestion against safe source snapshots;
- ingestion and extraction failure classification;
- stable source identity for imported source material;
- local test users or clients with different source scopes;
- one shared read boundary used by CLI ask and reserved for MCP/chat later;
- scoped answer, search, source lookup, citation, and gap behavior;
- regression tests proving restricted material does not leak.

It should not ship MCP, Research Chat, temporal fact migration, or ontology
suggestion review yet.

## What Already Exists

SourcyAvo should reuse these pieces instead of rebuilding parallel flows:

- `src/db.ts` already defines the local SQLite memory database, source records,
  memory chunks, relationships, semantic facts, extraction runs, and ingest
  errors.
- `src/ingest.ts` already supports recursive ingestion, per-file error logging,
  source records, chunking, embeddings, and citations.
- `src/refresh.ts` already owns extraction caching, entity/relationship/fact
  rebuilds, conflict marking, stale fact restoration, and source provenance.
- `src/answer.ts` already owns the four-section answer contract and should be
  wrapped by the new shared read boundary rather than copied for MCP or chat.
- `docs/adr/0001-permissioned-memory-layer.md` already decides that prompt-only
  secrecy and post-retrieval filtering are not acceptable.

The current gap is not lack of source provenance. The current gap is that reads
do not take a caller identity or source scope.

## Shared Read Boundary

Checkpoint 2 should introduce one explicit read boundary before any new surface
is built.

Recommended shape:

```text
Caller identity
  -> AccessContext
       actor_type: user | oauth_client | test_client
       actor_id
       allowed_source_ids
       denied_source_ids
       audit_label
  -> MemoryReader
       ask(question, options)
       searchMemory(query, options)
       getSource(source_id)
       listGaps(options)
  -> Scoped SQL helpers
       where source_id in allowed_source_ids
       record audit_events for reads and denied reads
  -> Answer / Evidence / Gaps / Next Action
```

Rules:

- CLI, MCP, and Research Chat must call `MemoryReader`; they must not query
  `semantic_facts`, `memory_chunks`, or `source_records` directly.
- `buildSourcingMemoryAnswer` should either accept `AccessContext` or become an
  internal helper called only after scoped facts and chunks have been selected.
- Scoping must happen before retrieval, ranking, citation assembly, gap
  formatting, and answer synthesis.
- Test clients are enough for the next branch. A real OAuth provider is deferred
  until hosted HTTP MCP or Research Chat needs browser login.

## Stable Source Identity

The next branch should not rely on SQLite autoincrement IDs as the durable
permission identity. `source_records.id` can remain the internal primary key,
but permission tables and external callers need a stable text `source_id`.

Recommended model:

```text
source_records
  id              integer primary key
  source_id       text unique not null
  path            text unique not null
  title           text not null
  source_type     text not null
  content_hash    text not null
  raw_text        text not null

source_permissions
  principal_type  user | oauth_client | test_client
  principal_id    text not null
  source_id       text not null
  access          read

audit_events
  actor_type
  actor_id
  action          ask | search_memory | get_source | list_gaps | denied_read
  source_id
  created_at
```

`source_id` should be deterministic from the import taxonomy, not the transient
database row. For the real-data stress pass, use a simple explicit mapping file
or frontmatter override before inventing a complex source registry.

## Reviewed Next-Scope Data Flow

```text
Safe Codeology exports
  -> sourcyavo ingest
       -> source_records(source_id)
       -> memory_chunks(source_id via source_record_id)
       -> ingest_errors
  -> sourcyavo refresh
       -> extraction_runs
       -> entities / relationships / semantic_facts
       -> refresh failure categories
  -> MemoryReader(access_context)
       -> scoped facts
       -> scoped chunks
       -> scoped gaps
       -> scoped citations
  -> CLI ask
       -> Answer
       -> Evidence
       -> Gaps
       -> Next Action
```

The important property is that the same question can produce different evidence
for different test clients, and unauthorized source material never enters the
retrieval candidate set.

## Test Coverage Plan

Project test framework: Vitest, detected from `package.json` and
`vitest.config.ts`.

```text
CODE PATHS                                             USER FLOWS
[+] ingest real-data snapshot                          [+] Admin imports safe exports
  |-- [GAP] supported files continue after failures       |-- [GAP] bad file does not stop import
  |-- [GAP] failure reason categories are persisted       |-- [GAP] import report names skipped files
  `-- [GAP] stable source_id assigned per source          `-- [GAP] source taxonomy is inspectable

[+] MemoryReader(access_context)                       [+] Scoped CLI ask
  |-- [GAP] allowed source facts are returned             |-- [GAP] same question differs by client
  |-- [GAP] denied source facts are excluded              |-- [GAP] restricted citation is hidden
  |-- [GAP] denied chunks are excluded before ranking      |-- [GAP] restricted gap is hidden
  |-- [GAP] getSource denies unauthorized source_id        `-- [GAP] denied read is audited
  `-- [GAP] listGaps respects allowed source_ids

[+] answer assembly                                     [+] No-memory / no-access UX
  |-- [GAP] evidence only cites allowed facts/chunks       |-- [GAP] caller sees clear no-access gap
  |-- [GAP] gaps only mention allowed sources              `-- [GAP] no silent empty answer
  `-- [GAP] next action cannot mention denied material

COVERAGE TARGET: every path above covered before MCP work starts.
QUALITY TARGET: behavior + edge + error tests for permission paths.
```

Required tests:

- Unit tests for `AccessContext` construction and allowed-source resolution.
- Unit tests for scoped SQL helpers with empty, single-source, multi-source, and
  unknown-source scopes.
- Regression tests showing current unscoped `ask` behavior would leak restricted
  accepted facts, chunks, citations, and gaps.
- Integration tests that seed two sources, two test clients, and the same
  question, then assert different scoped answers.
- `getSource` tests for allowed, denied, missing, and malformed source IDs.
- Audit tests for successful reads and denied reads.
- Real-data stress tests that classify misses as extraction, retrieval,
  missing-source, temporal, citation, or permission failures.

## Failure Modes

- Bad export file during ingestion: covered if `ingest_errors` records a reason
  and neighboring files still ingest. User should see the skipped file and
  reason in the stress report.
- Source ID changes after reimport: not covered until stable `source_id` tests
  exist. This can silently break permissions, so it is a critical next-scope
  test.
- Restricted fact appears in `Answer`: not covered today because `answer.ts`
  reads accepted facts globally. The next branch must add a regression test.
- Restricted chunk appears in `Evidence`: not covered today because retrieval
  reads all chunks globally. The next branch must add a regression test.
- Restricted candidate/conflicted/stale fact appears in `Gaps`: not covered
  today because gap loading is global. The next branch must add a regression
  test.
- Caller has no allowed sources: should return a clear no-access/no-memory style
  answer, not a silent empty answer.
- Real-data extraction fails on one source type: should be classified in the
  stress report and should not prevent other sources from being refreshed.

## Performance Review

No production-scale performance work is needed in the next branch. The current
local SQLite shape is boring and appropriate.

One guardrail is required: source filtering must be pushed into SQL before
ranking and embedding similarity. Do not retrieve all chunks and filter in
JavaScript for scoped callers. That would be both a leak risk and a needless
performance cost.

Add indexes when the permission schema lands:

- `source_records(source_id)`
- `source_permissions(principal_type, principal_id, source_id)`
- `memory_chunks(source_record_id)`
- `semantic_facts(source_record_id, status)`
- `audit_events(actor_type, actor_id, created_at)`

## NOT In Scope For The Next Branch

- MCP server implementation: defer until scoped read behavior is proven in CLI
  tests.
- Research Chat: defer until MCP/read service semantics are stable.
- Real OAuth provider selection: use local test clients and `AccessContext`
  first; hosted auth is a later integration decision.
- Temporal fact migration: keep the roadmap direction, but do not mix temporal
  semantics into the permission branch.
- Ontology suggestions: defer until real-data stress failures prove which
  ontology gaps repeat.
- Full graph traversal: defer broad traversal; current sourcing-specific
  relationships are enough for the next branch.
- Distribution pipeline: no new binary, package, container, or hosted app is
  introduced in the next branch.

## Worktree Parallelization Strategy

The next branch has three possible lanes after a short schema/API alignment
step.

| Step | Modules touched | Depends on |
|---|---|---|
| Stable source IDs and permissions schema | `src/db.ts`, `src/ingest.ts` | none |
| Scoped read boundary | `src/answer.ts`, new read-service module | source IDs |
| Real-data stress harness and reporting | `tests/`, docs/spec fixtures | source IDs |

Recommended execution:

- Lane A: stable source IDs and permission schema.
- Lane B: scoped read boundary after Lane A.
- Lane C: real-data stress harness can start in parallel with Lane B once source
  taxonomy fixtures are agreed.

Conflict flag: Lane A and Lane B both touch the memory schema/read path, so they
should either run sequentially or merge Lane A first.

## Implementation Tasks

Synthesized from this eng review. Each task derives from a concrete finding
above.

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — Scope — Rewrite the next
  implementation plan around Checkpoint 1 plus the permission choke point.
  - Surfaced by: Step 0 scope challenge.
  - Files: `docs/superpowers/specs/2026-06-07-sourcyavo-team-memory-roadmap-design.md`
  - Verify: plan names MCP, Research Chat, temporal migration, and ontology
    suggestions as out of scope for the next branch.

- [ ] **T2 (P1, human: ~2h / CC: ~20min)** — Permissions — Add the
  `MemoryReader` / shared read boundary and require CLI/MCP/chat to use it.
  - Surfaced by: Architecture review.
  - Files: `src/answer.ts`, new read-service module, tests.
  - Verify: scoped answer/search/source/gap tests pass.

- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — Source Identity — Add stable text
  `source_id` and source permission tables without replacing internal SQLite
  primary keys.
  - Surfaced by: Architecture and failure-mode review.
  - Files: `src/db.ts`, `src/ingest.ts`, tests.
  - Verify: reimport preserves source identity and permission mappings.

- [ ] **T4 (P1, human: ~3h / CC: ~30min)** — Tests — Add permission regression
  tests for facts, chunks, citations, gaps, source lookup, no-access answers,
  and audit events.
  - Surfaced by: Test review.
  - Files: `tests/answer.test.ts`, new permission/read-service tests.
  - Verify: `npm test`.

- [ ] **T5 (P2, human: ~3h / CC: ~30min)** — Real Data Stress — Add a repeatable
  safe real-data stress harness and classify misses by failure category.
  - Surfaced by: Test and verification review.
  - Files: tests or scripts for real-data snapshots, docs.
  - Verify: stress report classifies ingestion, extraction, retrieval,
    missing-source, temporal, citation, and permission misses.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | Optional for product direction |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | Outside voice skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clear | 5 issues converted into implementation tasks, 0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not run | Not needed for backend/CLI next scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | Not requested |

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED for the reduced next implementation scope:
  Checkpoint 1 plus the permission choke point.

## Execution Issues (Vertical Slices)

Generated by `/to-issues` on 2026-06-07. These break the approved next scope —
Checkpoint 1 (real-data stress) plus the smallest slice of Checkpoint 2
(source-scoped read enforcement) — into tracer-bullet vertical slices ready for
workflow-style parallel agent execution. MCP, Research Chat, temporal facts, and
ontology suggestions are out of scope for these issues.

Dependency DAG: `S1 -> S2 -> {S3, S4}`, `S4 -> S5`, `S4 -> S6` (S1, S2 also feed
S6). After S1 and S2 land, three lanes run in parallel: Lane A = S3, Lane B = S4
then S5, Lane C = S6 (starts once S4 lands).

---

### S1 — Decision: source taxonomy, deterministic source_id, and test corpus

**Type:** HITL — **RESOLVED 2026-06-07**

**Decision:**
- `source_id` = frontmatter `source_id:` override if present, else a slug of the
  stable relative path (e.g. `spring-2026/cold-emailing/apollo-csv`). Never the
  SQLite autoincrement `id`.
- Test corpus = a committed synthetic, Codeology-shaped fixture set under
  `tests/fixtures/stress/` (safe for CI; no private contacts). A `--snapshot`
  flag points the stress harness at a real safe snapshot when one is available.

#### What to build

Agree the foundational data decisions that every later slice depends on. Decide
the deterministic `source_id` derivation rule (frontmatter override plus a stable
path/taxonomy-based fallback — explicitly NOT the SQLite autoincrement `id`),
define the source taxonomy for the first real Codeology import, and pick the safe
real-data snapshot that becomes the repeatable test corpus. Capture the outcome
as a short committed decision note plus the fixture/snapshot source files that
S2 and S6 build against. Prefer a simple explicit mapping file or frontmatter
override over a complex source registry.

#### Acceptance criteria

- [ ] Deterministic `source_id` rule is written down and is stable across reimport.
- [ ] First-import source taxonomy is defined and inspectable.
- [ ] A safe real-data snapshot is selected and committed as the repeatable test corpus.
- [ ] No restricted/private material is included in the committed corpus.

#### Blocked by

None - can start immediately.

---

### S2 — Stable source_id + permission/audit schema + scoped ingest

**Type:** AFK

#### What to build

Add durable source identity and the permission/audit foundation end-to-end. Add
a `source_records.source_id` (text, unique) column populated by ingest using the
S1 derivation rule, plus `source_permissions` (principal_type/principal_id/
source_id/access) and `audit_events` tables, with the indexes named in the
Performance Review. Reimporting the same source must preserve its `source_id` and
therefore its permission mappings. Internal SQLite primary keys stay as-is; only
the external permission identity becomes the stable text `source_id`.

#### Acceptance criteria

- [ ] Ingest assigns a deterministic, stable `source_id` per source.
- [ ] Reimport of the same source preserves `source_id` and any permission mappings.
- [ ] `source_permissions` and `audit_events` tables and indexes exist.
- [ ] Permissions can be seeded for at least two test principals against different sources.
- [ ] Tests cover stable-ID-on-reimport and the empty/single/multi/unknown source-scope cases.

#### Blocked by

- S1 (source taxonomy, deterministic source_id, and test corpus).

---

### S3 — Ingest robustness + failure classification (Checkpoint 1)

**Type:** AFK

#### What to build

Harden the ingest path so a single bad export file cannot stop the run. Each
failure is recorded in `ingest_errors` with a categorized, actionable reason, and
the import produces a report that names every skipped file with its reason.
Neighboring files continue to ingest after a failure.

> Shares `ingest.ts` with S2 — sequence after S2; do not run concurrently.

#### Acceptance criteria

- [ ] One malformed file does not halt ingestion of the remaining files.
- [ ] Every ingest failure is persisted to `ingest_errors` with a categorized reason.
- [ ] The import report lists each skipped file and why it was skipped.
- [ ] Tests cover a mixed batch where good and bad files are processed in the same run.

#### Blocked by

- S2 (stable source_id + permission/audit schema + scoped ingest).

---

### S4 — MemoryReader + AccessContext scoped `ask` (tracer bullet)

**Type:** AFK

#### What to build

Build the shared read boundary and prove the core permission primitive
end-to-end. Introduce a read-service module owning `AccessContext` (actor_type,
actor_id, allowed_source_ids, denied_source_ids, audit_label) and a `MemoryReader`
whose `ask` routes the existing four-section answer logic through scoped queries.
Source filtering must be pushed into SQL before retrieval and ranking for BOTH
accepted facts and evidence chunks — never retrieve everything and filter in
JavaScript. Wire CLI `ask --client <id>` (or equivalent) to construct an
`AccessContext` and call `MemoryReader`. The same question asked by two test
clients with different scopes must return different allowed Answer + Evidence,
and a restricted accepted fact or chunk must never appear for an unauthorized
caller.

#### Acceptance criteria

- [ ] `AccessContext` and `MemoryReader.ask` exist; CLI `ask` routes through them.
- [ ] Scope filtering happens in SQL before retrieval/ranking for facts and chunks.
- [ ] Two test clients asking the same question get different scoped Answer + Evidence.
- [ ] Regression test proves a restricted accepted fact/chunk does not leak into Answer or Evidence.
- [ ] `buildSourcingMemoryAnswer` is only reachable after scoping (wrapped, not bypassed).

#### Blocked by

- S2 (stable source_id + permission/audit schema + scoped ingest).

---

### S5 — Remaining scoped surfaces + audit + no-access UX

**Type:** AFK

#### What to build

Extend the read boundary to the remaining surfaces so every read respects scope.
Add scoped `searchMemory`, `getSource` (deny unauthorized, missing, and malformed
`source_id`), and `listGaps`/Gaps that exclude restricted candidate/conflicted/
stale facts. A caller with no allowed sources gets a clear no-access answer rather
than a silent empty result. Every read and every denied read is recorded in
`audit_events`.

> Shares the read-service module with S4 — sequence after S4.

#### Acceptance criteria

- [ ] `searchMemory`, `getSource`, and `listGaps` all enforce the caller's scope.
- [ ] `getSource` denies unauthorized, missing, and malformed `source_id` inputs.
- [ ] Restricted candidate/conflicted/stale facts are hidden from Gaps.
- [ ] A no-allowed-sources caller receives an explicit no-access answer, never a silent empty one.
- [ ] Successful reads and denied reads are written to `audit_events` and covered by tests.

#### Blocked by

- S4 (MemoryReader + AccessContext scoped `ask`).

---

### S6 — Real-data stress harness + benchmark miss classification

**Type:** AFK

#### What to build

Add a repeatable, safe real-data stress harness that runs the committed snapshot
through ingest, refresh, and scoped `ask` over the benchmark sourcing questions,
then classifies every miss by category: extraction, retrieval, missing-source,
temporal, citation, or permission. Output a stress report that surfaces skipped
files and categorized misses instead of burying them in logs.

#### Acceptance criteria

- [ ] The harness runs the committed real-data snapshot repeatably without manual edits.
- [ ] Benchmark sourcing questions are executed through the scoped read boundary.
- [ ] Each miss is classified as extraction, retrieval, missing-source, temporal, citation, or permission.
- [ ] The stress report names skipped files and categorized misses.

#### Blocked by

- S4 (MemoryReader + AccessContext scoped `ask`); benefits from S1 and S2.
