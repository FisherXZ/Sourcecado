# SourcyAvo Architecture Design

Date: 2026-06-04
Status: Draft

## Summary

SourcyAvo starts as a local-first sourcing memory layer that can be tested directly from Codex or Claude Code before a web app exists. The first milestone is a CLI that ingests exported source files, refreshes memory, and answers sourcing questions with citations, gaps, and next actions.

The architecture deliberately builds memory first and interface second. The later web app and agent harness should consume the same memory layer rather than owning separate retrieval logic.

## Reference Systems

SourcyAvo uses two primary references:

- **LangGraph / LangMem** for the memory taxonomy: short-term context, long-term semantic memory, episodic memory concepts, and procedural memory.
- **GBrain** for the product behavior: source-grounded synthesis, citations, relationship graph traversal, gap analysis, and recurring memory refresh.

SourcyAvo should preserve the load-bearing ideas from these systems without copying their full scale or operational complexity.

## Goals

- Let a Sourcing Director ask real sourcing-history questions faster than manually searching Notion, Drive, Sheets, and email.
- Teach the shape of a modern agent memory layer without turning the project into a full GBrain clone.
- Keep the first implementation local-first and testable from Codex or Claude Code.
- Preserve a clean path from local CLI to agent tools and eventually to a web Research Chat.

## Non-Goals

- No live Notion, Google Drive, Gmail, or Messenger connectors in the first milestone.
- No web dashboard or CRM-style management surface in the first milestone.
- No autonomous outreach or automatic email/message sending.
- No dedicated graph database.
- No per-person permission model.
- No full codebase indexing.
- No separate AnswerTrace table.
- No separate SourcingEpisode table.
- No DB-backed ProcedureMemory model.

## System Shape

```text
seed-data/
  exported source files

sourcyavo ingest seed-data/
  -> SourceRecords
  -> MemoryChunks

sourcyavo refresh
  -> Entities
  -> Relationships
  -> SemanticFacts
  -> clean aliases, conflicts, stale facts

sourcyavo ask "Who needs follow-up?"
  -> retrieve chunks
  -> use entities, relationships, and semantic facts
  -> load procedure docs
  -> answer with citations, gaps, and next action
```

The CLI is the first product surface because it is the simplest way to test the memory layer locally. The web app should come after the memory layer is working.

## CLI Surface

The MVP CLI has three commands:

```bash
sourcyavo ingest seed-data/
sourcyavo refresh
sourcyavo ask "Who did we contact last semester for AI safety?"
```

`ingest` reads exported source files and creates source-backed chunks.

`refresh` runs the learn and clean loop. It extracts structured memory from chunks, merges obvious duplicates, resolves aliases, marks conflicts, and marks stale facts.

`ask` retrieves relevant chunks and structured memory, loads procedure docs, and returns a Sourcing Memory Answer.

## Source Input

MVP ingestion accepts a simple folder of exported files:

```text
seed-data/
  spring-2026-sourcing.md
  outreach-tracker.csv
  ai-safety-thread.eml
  speaker-notes.txt
```

Source type can be inferred from extension or simple metadata. The MVP should not require a strict folder taxonomy.

## Storage

Use one local memory database plus raw source files.

```text
seed-data/
  raw exported files

.sourcyavo/
  memory.db
```

The local database should hold the learned memory. Raw source files remain readable and citeable. The schema should be conceptually portable to Postgres and pgvector later, but the first version should not require hosted database setup.

## Memory Model

The MVP has five DB-backed records and one file-backed procedure layer.

### SourceRecord

A trusted source item from exported files. Examples include Notion markdown, Google Sheet CSV exports, Drive text or markdown exports, and email thread exports.

SourceRecords preserve where memory came from.

### MemoryChunk

A searchable piece of a SourceRecord. A MemoryChunk stores text, embedding metadata, a citation pointer, and permission tier.

MemoryChunks support semantic retrieval and citations.

### Entity

A normalized thing SourcyAvo can reason about.

Entity types:

- person
- organization
- project
- event
- semester
- domain

Entities may have aliases so "Jane Doe", "Jane", and an email address can refer to the same person.

### Relationship

A typed edge between entities.

Example relationship types:

- `works_at`
- `contacted`
- `responded`
- `worked_with`
- `needs_follow_up`
- `associated_with`
- `relevant_to_domain`

Relationships should point back to source evidence when possible.

### SemanticFact

A clean extracted claim tied to source evidence.

Example:

```text
subject: Jane Doe
predicate: open_to_speaking
object: AI safety event
source: ai-safety-thread.eml
confidence: 0.78
status: candidate
```

SemanticFacts are auto-extracted. There is no human review screen in the MVP.

Allowed statuses:

- `candidate`
- `accepted`
- `conflicted`
- `stale`

Low-confidence facts can inform uncertainty or Knowledge Gap language, but they must not be stated as clean claims.

## Procedure Memory

Procedure memory is not a database model in the MVP. It lives in markdown/context files, similar to `CLAUDE.md` or `AGENTS.md`.

Recommended files:

```text
procedures/
  SOURCYAVO.md
  answer-format.md
  citation-rules.md
  gap-analysis.md
  outreach-tone.md
  memory-refresh.md
```

These files define how SourcyAvo behaves:

- answer format
- citation requirements
- gap-analysis rules
- outreach tone
- memory refresh rules

The `ask` command loads relevant procedure docs before generating a Sourcing Memory Answer.

## Learn And Clean Loop

The memory lifecycle is one command from the user perspective:

```bash
sourcyavo refresh
```

Internally, refresh has two phases.

### Learn

The learn phase extracts memory from source-backed chunks:

- entities
- relationships
- semantic facts

### Clean

The clean phase improves extracted memory:

- merges obvious duplicate entities
- resolves aliases
- marks conflicting facts
- marks stale facts
- promotes high-confidence non-conflicting facts to accepted

There is no human-in-the-loop approval step for the MVP. Uncertainty should flow into answer gaps rather than blocking refresh.

## Answer Contract

Every answer should use this shape:

```text
Answer
Evidence
Gaps
Next Action
```

Rules:

- Factual claims need citations.
- Missing or stale evidence should appear under Gaps.
- Low-confidence facts can be mentioned only as uncertainty.
- Conflicting facts should be surfaced as gaps or conflicts.
- If no relevant memory exists, the answer should say so clearly.

Example:

```text
Answer:
We contacted three AI safety contacts in Spring 2026. Jane Doe replied positively and appears to need follow-up.

Evidence:
- ai-safety-thread.eml
- spring-2026-sourcing.csv

Gaps:
I do not see a final outcome for Jane's follow-up. Messenger may have more context.

Next Action:
Verify whether Jane was added to the speaker planning thread, then draft a follow-up.
```

## Permission Model

The MVP uses three role-level tiers:

- Public Club Context
- Officer Context
- Restricted Sourcing Context

Permissioning is required for trust, but it is not the product's top-level promise. Restricted material should be separated at the source/file level and again through metadata on chunks and learned records.

The model should filter restricted records before retrieval. The LLM should not receive context the current user is not allowed to see.

## Agent Harness Path

After the CLI memory layer works, expose the same memory layer through agent tools.

Candidate tools:

- `search_sourcing_memory`
- `get_contact_context`
- `answer_sourcing_question`
- `refresh_memory`

The agent harness should consume the memory layer. It should not own ingestion or cleaning by default.

## Web App Path

The first web app should be a thin Research Chat over the memory layer.

Deferred UI:

- dashboards
- source browsers
- admin panels
- CRM-style contact pages

The web app should not create a separate retrieval system. It should call the same answer path used by the CLI.

## Error Handling

- Unreadable file: skip the file, log the error, and continue ingestion.
- Unsupported file: record as skipped with a reason.
- Empty file: create no chunks and log a warning.
- Failed extraction: keep SourceRecord and MemoryChunks; mark extraction as failed.
- Missing citation: do not present the claim as fact.
- Low-confidence fact: use only as uncertainty.
- Conflicting facts: surface as a Knowledge Gap.
- Empty memory database: answer that no indexed sources exist yet.

## Testing Strategy

Tests should prove the memory layer works on seed data.

Required coverage:

- ingest creates SourceRecords and MemoryChunks
- refresh extracts expected Entities
- refresh extracts expected Relationships
- refresh extracts SemanticFacts with source evidence
- duplicate names merge or alias correctly
- ask returns Answer, Evidence, Gaps, and Next Action
- low-confidence facts do not become clean claims
- restricted records are filtered before retrieval
- empty memory returns a clear no-sources answer

## Open Questions

These are implementation-plan questions, not blockers to the architecture direction.

- Which local database should be used for the first implementation?
- Which embedding provider should be used for local testing?
- Which file formats are required in the first seed dataset?
- What exact permission metadata should seed files use?
- Should `refresh` be deterministic enough for snapshot tests, or should tests mock extraction?

## Approval

Approved architecture direction: Lean Memory Layer CLI.

The next step after this design is reviewed is to write an implementation plan.
