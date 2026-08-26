# SourcyAvo Memory Layer CLI Implementation Plan

Date: 2026-06-04
Eng review updated: 2026-06-05
Status: Reviewed, ready to implement with guardrails

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task-by-task. Use `superpowers:test-driven-development` for each implementation slice.

## Goal

Build a local-first SourcyAvo memory-layer CLI that ingests exported sourcing files, refreshes structured memory, and answers sourcing-history questions with citations, gaps, and next actions.

The MVP is **single-user local memory**. It does not claim app-level permission enforcement.

## Scope Decision

Proceed with the full memory-layer CLI shape rather than reducing to basic RAG. The plan intentionally keeps the load-bearing memory primitives:

- source records
- chunks
- semantic facts
- entities
- relationships
- procedure docs
- learn and clean refresh
- cited answers

The guardrail is that the implementation must not be a hardcoded seed-data demo. It must generalize beyond Jane/Anthropic fixtures.

## Architecture

```text
seed-data/
  exported .md/.txt/.csv/.eml files
        |
        v
sourcyavo ingest seed-data/
        |
        v
source_records -> memory_chunks
        |
        v
sourcyavo refresh
        |
        +--> CSV/sheet deterministic extraction
        +--> Markdown/email LLM structured extraction
        +--> extraction cache keyed by content + extractor inputs
        +--> clean/consolidate candidates
        |
        v
entities + relationships + semantic_facts
        |
        v
sourcyavo ask "Who needs follow-up?"
        |
        v
Answer / Evidence / Gaps / Next Action
```

## MVP Defaults

- Runtime: TypeScript + Node.js.
- Database: local SQLite at `.sourcyavo/memory.db`.
- SQLite library: `better-sqlite3`.
- Tests: Vitest.
- Embeddings: deterministic local hashed token vectors for MVP retrieval tests.
- Source formats: `.md`, `.txt`, `.csv`, `.eml`.
- Unstructured extraction: LLM structured extraction behind an interface.
- CI/test extraction: mocked extractor, no live model calls in tests.
- Procedure memory: markdown files under `procedures/`.
- Permission enforcement: deferred.

## NOT In Scope

- Web app or Research Chat UI.
- Live Notion, Drive, Sheets, Gmail, or Messenger connectors.
- Autonomous outreach or sending messages.
- Multi-user auth or permission enforcement.
- Per-person permissions.
- Dedicated graph database.
- Hosted vector database.
- Production eval suite for LLM extraction quality.
- Full GBrain clone.

## What Already Exists

- [CONTEXT.md](/Users/fisher/Documents/GitHub2026/Sourcecado/CONTEXT.md) defines the domain language.
- [docs/designs/2026-06-04-sourcyavo-office-hours-design.md](/Users/fisher/Documents/GitHub2026/Sourcecado/docs/designs/2026-06-04-sourcyavo-office-hours-design.md) locks the product framing.
- [docs/superpowers/specs/2026-06-04-sourcyavo-architecture-design.md](/Users/fisher/Documents/GitHub2026/Sourcecado/docs/superpowers/specs/2026-06-04-sourcyavo-architecture-design.md) defines the memory architecture.
- [docs/adr/0001-permissioned-memory-layer.md](/Users/fisher/Documents/GitHub2026/Sourcecado/docs/adr/0001-permissioned-memory-layer.md) remains future direction, but permission enforcement is not in this MVP.

There is no app code yet. Implementation starts from an empty codebase.

## File Structure

Create:

- `package.json`
- `package-lock.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/types.ts`
- `src/db.ts`
- `src/frontmatter.ts`
- `src/chunk.ts`
- `src/embeddings.ts`
- `src/ingest.ts`
- `src/extractors/types.ts`
- `src/extractors/csv.ts`
- `src/extractors/llm.ts`
- `src/extractors/mock.ts`
- `src/refresh.ts`
- `src/procedures.ts`
- `src/answer.ts`
- `src/cli.ts`
- `procedures/SOURCYAVO.md`
- `procedures/answer-format.md`
- `procedures/citation-rules.md`
- `procedures/gap-analysis.md`
- `procedures/outreach-tone.md`
- `procedures/memory-refresh.md`
- `tests/fixtures/seed-data/spring-2026-sourcing.md`
- `tests/fixtures/seed-data/outreach-tracker.csv`
- `tests/fixtures/seed-data/ai-safety-thread.eml`
- `tests/fixtures/seed-data/duplicate-aliases.csv`
- `tests/fixtures/seed-data/conflicting-status.csv`
- `tests/types.test.ts`
- `tests/db.test.ts`
- `tests/ingest.test.ts`
- `tests/extractors.test.ts`
- `tests/refresh.test.ts`
- `tests/answer.test.ts`
- `tests/cli.test.ts`
- `README.md`
- `seed-data/.gitkeep`

## Data Model

```text
source_records
  id
  path unique
  title
  source_type
  content_hash
  raw_text
  created_at
  updated_at

memory_chunks
  id
  source_record_id
  chunk_index
  text
  chunk_hash
  embedding
  citation
  created_at

extraction_runs
  id
  source_chunk_id
  cache_key unique
  chunk_hash
  extractor_type
  extractor_version
  prompt_hash
  schema_version
  model_name
  raw_output
  parsed_candidates_json
  status
  error
  created_at

entities
  id
  type
  name
  canonical_key unique
  created_at

entity_aliases
  id
  entity_id
  alias
  alias_key unique

relationships
  id
  from_entity_id
  to_entity_id
  type
  source_record_id
  source_chunk_id
  confidence
  note
  created_at

semantic_facts
  id
  subject
  predicate
  object
  source_record_id
  source_chunk_id
  confidence
  status
  created_at

ingest_errors
  id
  path
  reason
  created_at
```

Do not add permission columns in the MVP. Keep source provenance and citations so permissioning can be layered on later without losing lineage.

## Extraction Cache

Use a mature content-addressed cache pattern. The cache key must include all inputs that affect extraction output:

```text
cache_key =
  chunk_hash
  + extractor_type
  + extractor_version
  + prompt_hash
  + schema_version
  + model_name
```

This follows the same idea as LangChain-style indexing records and Bazel-style action caching: unchanged inputs reuse prior outputs.

## Extractor Contract

All extractors return candidate memory in one shape:

```ts
interface ExtractedCandidate {
  kind: "entity" | "relationship" | "semantic_fact";
  subject?: string;
  predicate?: string;
  object?: string;
  entityType?: EntityType;
  relationshipType?: string;
  confidence: number;
  evidenceText: string;
}
```

CSV/sheet extraction is deterministic. Markdown/email extraction uses an LLM, validates structured JSON, and returns candidates. Tests use the mock extractor.

## Clean And Consolidate

`refresh` must do both phases:

```text
learn
  -> extract candidate entities, relationships, semantic facts

clean
  -> normalize names and predicates
  -> merge duplicate entities and aliases
  -> dedupe duplicate facts
  -> mark conflicting facts as conflicted
  -> keep low-confidence facts as candidate
  -> promote high-confidence non-conflicting facts to accepted
```

`markConflictsAndStaleFacts()` must not be an empty stub.

## CLI

Commands:

```bash
npm run sourcyavo -- ingest seed-data/
npm run sourcyavo -- refresh
npm run sourcyavo -- ask "Who needs follow-up for AI safety?"
```

No `--permission` flag in the MVP.

For LLM extraction, `refresh` should require model configuration only when it encounters unstructured source types that need LLM extraction. CSV-only refresh should work without an API key.

## Task 1: Bootstrap TypeScript CLI Project

- [ ] Create `package.json`, `tsconfig.json`, `vitest.config.ts`.
- [ ] Add `better-sqlite3`, `typescript`, `tsx`, `vitest`, and Node/better-sqlite3 types.
- [ ] Create `src/types.ts` with source types, entity types, fact statuses, relationship types.
- [ ] Write `tests/types.test.ts`.
- [ ] Run `npm install`.
- [ ] Run `npm test`.

## Task 2: Add SQLite Schema

- [ ] Create `src/db.ts` with `createDatabase()` and migrations.
- [ ] Add all tables listed in Data Model.
- [ ] Enable SQLite foreign keys and WAL.
- [ ] Write `tests/db.test.ts`.
- [ ] Test that schema creates every table and required uniqueness constraints.

## Task 3: Add Procedure Memory

- [ ] Create procedure markdown files under `procedures/`.
- [ ] Create `src/procedures.ts`.
- [ ] Write `tests/procedures.test.ts`.
- [ ] Ensure `ask` can load procedure docs but answer correctness does not depend on docs existing.

## Task 4: Implement Ingestion

- [ ] Create `src/frontmatter.ts`, `src/chunk.ts`, `src/embeddings.ts`, `src/ingest.ts`.
- [ ] Support `.md`, `.txt`, `.csv`, `.eml`.
- [ ] Default source type from extension when metadata is missing.
- [ ] Store content hashes on source records and chunk hashes on memory chunks.
- [ ] Catch read, parse, and chunk errors per file.
- [ ] Log skipped/bad files to `ingest_errors`.
- [ ] Do not let one bad file abort the whole folder.
- [ ] Write tests for happy path, unsupported file, empty file, and unreadable/malformed file behavior.

## Task 5: Implement Extractors

- [ ] Create extractor contract in `src/extractors/types.ts`.
- [ ] Create deterministic CSV/sheet extractor.
- [ ] Create LLM extractor for Markdown/email structured extraction.
- [ ] Create mock extractor for tests.
- [ ] Validate LLM output before storing candidates.
- [ ] Handle malformed LLM JSON with an extraction error, not a crash.
- [ ] Add tests for CSV generic extraction using non-Jane fixture data.
- [ ] Add contract tests for mocked LLM extraction.
- [ ] Add tests for missing model config when unstructured sources require LLM extraction.

## Task 6: Implement Refresh, Cache, And Clean

- [ ] Create `src/refresh.ts`.
- [ ] Use `extraction_runs` to avoid repeated extraction for unchanged chunks.
- [ ] Cache by chunk hash, extractor type/version, prompt hash, schema version, and model name.
- [ ] Reuse cached candidates on refresh.
- [ ] Implement clean/consolidate.
- [ ] Insert entities, aliases, relationships, and semantic facts with source ids.
- [ ] Test duplicate fact dedupe.
- [ ] Test alias merge.
- [ ] Test conflicting fact status.
- [ ] Test low-confidence facts remain `candidate`.
- [ ] Test idempotent refresh does not call extractor twice for unchanged chunks.

## Task 7: Implement Retrieval And Answers

- [ ] Create `src/answer.ts`.
- [ ] Retrieve relevant chunks by deterministic vector similarity.
- [ ] Retrieve accepted semantic facts related to the question.
- [ ] Format every response with `Answer`, `Evidence`, `Gaps`, and `Next Action`.
- [ ] Cite source records/chunks for factual claims.
- [ ] Surface candidate/conflicted/stale facts under `Gaps`.
- [ ] Return a clear no-memory answer when no chunks exist.
- [ ] Test answer format, citations, no relevant memory, and candidate/conflicted gap behavior.

## Task 8: Wire CLI

- [ ] Create `src/cli.ts`.
- [ ] Implement `ingest`, `refresh`, and `ask`.
- [ ] Add CLI smoke test that runs all three commands against a temp DB.
- [ ] Build with `npm run build`.

## Task 9: Documentation And Verification

- [ ] Update `README.md`.
- [ ] Add `seed-data/.gitkeep`.
- [ ] Document local-only MVP and warn users to ingest only files they are allowed to use.
- [ ] Document LLM config needed for Markdown/email extraction.
- [ ] Run `npm test`.
- [ ] Run `npm run build`.
- [ ] Run manual CLI verification:

```bash
rm -rf .sourcyavo
npm run sourcyavo -- ingest tests/fixtures/seed-data
npm run sourcyavo -- refresh
npm run sourcyavo -- ask "Who needs follow-up for AI safety?"
```

## Test Plan

```text
CODE PATHS                                      USER FLOWS
[+] CLI                                         [+] Local memory setup
  ├── ingest happy path                           ├── ingest fixtures
  ├── refresh happy path                          ├── refresh memory
  └── ask happy path                              └── ask follow-up question

[+] Ingestion
  ├── md/csv/eml source creation
  ├── bad file does not abort folder
  ├── unsupported file logs error
  └── empty file logs error

[+] Extraction
  ├── CSV generic non-Jane row extraction
  ├── mocked LLM md/email structured extraction
  ├── malformed LLM JSON handled cleanly
  └── missing API key behavior for unstructured files

[+] Clean/consolidate
  ├── duplicate facts merge
  ├── aliases merge
  ├── conflicting facts marked conflicted
  └── low-confidence facts remain candidate

[+] Answer
  ├── four-section answer format
  ├── cited fact source ids
  └── no relevant memory answer
```

## Failure Modes

| Flow | Failure | Test Required | Handling Required | User Experience |
|---|---|---:|---:|---|
| ingest | One bad file in folder | yes | yes | Reports skipped file; continues ingesting others |
| ingest | Empty source file | yes | yes | Logs ingest error; no chunks created |
| refresh | Missing LLM config for Markdown/email | yes | yes | Clear error naming required config |
| refresh | Malformed LLM JSON | yes | yes | Extraction error recorded; refresh continues |
| refresh | Unchanged chunk | yes | yes | Uses cache; no repeated extraction |
| clean | Duplicate fact | yes | yes | One canonical fact retained |
| clean | Conflicting status | yes | yes | Fact marked conflicted and surfaced as gap |
| answer | No memory exists | yes | yes | Clear no-sources response |

No accepted failure mode may be silent.

## Parallelization Strategy

| Step | Modules touched | Depends on |
|---|---|---|
| Bootstrap/schema | project root, `src/db.ts`, `src/types.ts` | — |
| Procedure memory | `procedures/`, `src/procedures.ts` | bootstrap |
| Ingestion | `src/frontmatter.ts`, `src/chunk.ts`, `src/embeddings.ts`, `src/ingest.ts` | schema |
| Extractors | `src/extractors/` | types |
| Refresh/cache/clean | `src/refresh.ts`, `src/extractors/`, `src/db.ts` | schema, ingestion, extractors |
| Answer | `src/answer.ts`, `src/procedures.ts` | schema, refresh |
| CLI/docs | `src/cli.ts`, `README.md` | ingestion, refresh, answer |

Parallel lanes:

- Lane A: bootstrap/schema -> ingestion.
- Lane B: procedure memory.
- Lane C: extractors.
- Lane D: refresh/cache/clean after A + C.
- Lane E: answer + CLI after D.
- Lane F: docs/final verification after E.

Conflict flags: Lane A and D both touch DB shape; merge schema changes before refresh/cache work.

## Issue Breakdown For Parallel Agents

These are tracer-bullet slices, not horizontal layer tickets. Each slice should leave the CLI more demoable than before.

### Readiness Legend

- **AFK Ready:** an agent can implement without more product input.
- **HITL Needed:** needs a human decision before parallel execution.
- **Blocked:** wait for another slice first.

### Proposed Slices

1. **Local Memory CLI Skeleton**
   - **Type:** AFK
   - **Ready for parallel agent:** yes
   - **Blocked by:** None
   - **User stories covered:** A developer can install, build, test, and run a local `sourcyavo` CLI against a SQLite memory database.
   - **What to build:** Bootstrap the TypeScript CLI, SQLite schema, procedure docs, and no-memory answer path.
   - **Acceptance criteria:**
     - [ ] `npm test` runs at least the type/schema/procedure tests.
     - [ ] `npm run build` succeeds.
     - [ ] `npm run sourcyavo -- ask "Who needs follow-up?"` returns a clear no-memory answer.

2. **CSV Sourcing Memory Tracer**
   - **Type:** AFK
   - **Ready for parallel agent:** yes
   - **Blocked by:** Slice 1
   - **User stories covered:** A Sourcing Director can ingest an exported tracker CSV, refresh memory, and ask who needs follow-up.
   - **What to build:** End-to-end CSV path through ingestion, deterministic extraction, refresh, clean storage, retrieval, answer formatting, and CLI smoke test.
   - **Acceptance criteria:**
     - [ ] CSV rows create source records, chunks, entities, relationships, and semantic facts.
     - [ ] Non-Jane fixture data answers correctly.
     - [ ] Answer includes `Answer`, `Evidence`, `Gaps`, and `Next Action`.

3. **Unstructured LLM Extraction Tracer**
   - **Type:** HITL
   - **Ready for parallel agent:** not yet
   - **Blocked by:** Slice 1, plus the LLM config decision below
   - **User stories covered:** A Sourcing Director can ingest Markdown/email exports and refresh them into structured sourcing memory.
   - **What to build:** LLM extractor interface, structured output validation, mocked LLM tests, malformed-output handling, and Markdown/email refresh path.
   - **Acceptance criteria:**
     - [ ] Markdown/email chunks are routed through an LLM extractor.
     - [ ] Tests use a mock extractor and make no live model calls.
     - [ ] Malformed LLM JSON records an extraction error and does not crash refresh.
     - [ ] Missing model config produces a clear error only when unstructured sources need LLM extraction.
   - **Unclear / not ready:** choose first LLM provider and config contract. Proposed default: OpenAI structured output with `OPENAI_API_KEY`, `SOURCYAVO_LLM_MODEL`, and a provider wrapper that can later swap to Anthropic/local.

4. **Clean And Consolidate Memory Tracer**
   - **Type:** AFK
   - **Ready for parallel agent:** yes after Slice 2
   - **Blocked by:** Slice 2
   - **User stories covered:** A Sourcing Director gets cleaner answers when source exports contain duplicate aliases or conflicting outreach status.
   - **What to build:** Alias merge, duplicate fact dedupe, conflict marking, low-confidence candidate retention, and gap surfacing in answers.
   - **Acceptance criteria:**
     - [ ] Duplicate entities/facts collapse to one canonical memory.
     - [ ] Conflicting facts are marked `conflicted`.
     - [ ] Low-confidence facts remain `candidate`.
     - [ ] Answers surface candidate/conflicted facts under `Gaps`.

5. **Extraction Cache Tracer**
   - **Type:** AFK
   - **Ready for parallel agent:** yes after Slice 2, best after Slice 3 if model fields are final
   - **Blocked by:** Slice 2; Slice 3 if implementing cache against live LLM metadata
   - **User stories covered:** A developer can rerun `sourcyavo refresh` without re-extracting unchanged chunks.
   - **What to build:** `extraction_runs` cache keyed by chunk hash plus extractor type/version, prompt hash, schema version, and model name.
   - **Acceptance criteria:**
     - [ ] Unchanged chunks reuse cached extraction candidates.
     - [ ] Changed chunks re-extract.
     - [ ] Changing extractor version, prompt hash, schema version, or model name invalidates the cache.
     - [ ] Idempotent refresh test proves extractor is not called twice for unchanged chunks.

6. **Final CLI Documentation And Verification**
   - **Type:** AFK
   - **Ready for parallel agent:** yes after Slices 2, 3, 4, and 5
   - **Blocked by:** Slices 2-5
   - **User stories covered:** A new contributor can run the MVP locally and understand its local-only security boundary.
   - **What to build:** README, seed-data placeholder, final manual verification, and local-only warning.
   - **Acceptance criteria:**
     - [ ] README documents install, ingest, refresh, ask, source formats, and LLM config.
     - [ ] README states this MVP is local-only and users should ingest only files they are allowed to use.
     - [ ] Full `npm test`, `npm run build`, and manual CLI verification pass.

### Dependency Graph

```text
Slice 1: Local Memory CLI Skeleton
        |
        +--> Slice 2: CSV Sourcing Memory Tracer
              |
              +--> Slice 4: Clean And Consolidate Memory Tracer
              |
              +--> Slice 5: Extraction Cache Tracer
        |
        +--> Slice 3: Unstructured LLM Extraction Tracer
              |
              +--> Slice 5: Extraction Cache Tracer

Slices 2, 3, 4, 5
        |
        v
Slice 6: Final CLI Documentation And Verification
```

### Parallel Execution Recommendation

Start Slice 1 first. After it lands, run Slice 2 and the LLM-config decision for Slice 3 in parallel. Once Slice 2 lands, Slice 4 can run. Slice 5 can start after Slice 2 with mock metadata, but it should wait for Slice 3 if the live LLM config shape is still changing.

Do not send Slice 3 to an AFK agent until the LLM provider/config contract is decided.

## Implementation Tasks

Synthesized from `/plan-eng-review` findings.

- [ ] **T1 (P1, human: ~2h / CC: ~20m)** — Extraction — Replace hardcoded refresh fixture logic with generic CSV extraction and LLM structured extraction for Markdown/email.
  - Surfaced by: Architecture Review Issue 2.
  - Files: `src/extractors/*`, `src/refresh.ts`, `tests/extractors.test.ts`, `tests/refresh.test.ts`.
  - Verify: `npm test -- tests/extractors.test.ts tests/refresh.test.ts`.

- [ ] **T2 (P1, human: ~1h / CC: ~12m)** — Ingestion — Isolate per-file ingestion failures and log errors.
  - Surfaced by: Code Quality Review Issue 3.
  - Files: `src/ingest.ts`, `tests/ingest.test.ts`.
  - Verify: `npm test -- tests/ingest.test.ts`.

- [ ] **T3 (P1, human: ~2h / CC: ~20m)** — Refresh — Add contract tests for mocked LLM extraction and clean/consolidate behavior.
  - Surfaced by: Test Review Issue 5.
  - Files: `src/extractors/*`, `src/refresh.ts`, `tests/extractors.test.ts`, `tests/refresh.test.ts`.
  - Verify: `npm test -- tests/extractors.test.ts tests/refresh.test.ts`.

- [ ] **T4 (P1, human: ~2h / CC: ~20m)** — Refresh cache — Add extraction cache keyed by chunk content and extractor inputs.
  - Surfaced by: Performance Review Issue 6.
  - Files: `src/db.ts`, `src/refresh.ts`, `tests/refresh.test.ts`.
  - Verify: idempotent refresh test proves unchanged chunks do not trigger extraction twice.

- [ ] **T5 (P2, human: ~45m / CC: ~8m)** — Scope cleanup — Remove MVP permission implementation and document local-only boundaries.
  - Surfaced by: User decision during review.
  - Files: `src/types.ts`, `src/cli.ts`, `src/answer.ts`, `README.md`, tests.
  - Verify: no `--permission` CLI path; README warns local-only.

## Deferred Work

- Permissioned multi-user memory layer.
- Live connectors.
- Agent tools over the memory layer.
- Thin Research Chat web app.
- Real embedding provider.
- Production LLM extraction eval suite.
- Richer temporal/staleness logic.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clear | 4 accepted issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | Backend CLI MVP |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | — |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement.
