# Feature A — Cited Sourcing Memory Answer: Design

Date: 2026-06-25 (revised after a grill-me reconciliation of the Claude Code
architecture with our local memory brain)
Status: Approved for implementation
Feature: A from `docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`
Blocked by: F2 (Postgres+pgvector), F3 (gateway embeddings), F5 (agent harness) — F5 on PR #7.
Branch: `feat/a-cited-memory-answer` (off `feat/f5-agent-harness`; rebase onto `main` after F5 merges).

## Purpose

A Sourcing Director asks a question in chat and gets a **cited answer with knowledge
gaps**, synthesized from imported sourcing notes in Postgres/pgvector — the first tier-1
measurable outcome, fully traced in the Run Ledger.

## The reconciliation (the core architectural decision)

We have a **mature local memory brain** (SQLite): chunking, embeddings, hybrid retrieval
(cosine gated by lexical), intent-aware fact ranking, and a `semantic_facts` lifecycle
(accepted / candidate / conflicted / stale) fed by an extraction pipeline with caching,
confidence-based acceptance, and conflict/staleness detection. **Claude Code** teaches a
different lesson: the agent reasons, tools fetch context on demand, and grounding lives in
tool results in the transcript — synthesis is the model's job, not a deterministic
template.

These reconcile cleanly by **splitting the brain at the synthesis seam**:

- The mature engine becomes the **retrieval + ranking + fact-lifecycle layer behind one
  tool** (`search_memory`). Nothing valuable is thrown away.
- The **model becomes the synthesizer** — it writes the cited answer from the tool's
  structured result. The deterministic prose templates (`answerLines`, `gapLines`, …) are
  the *only* part not ported; the model replaces them.

This keeps 100% of the retrieval/ranking/fact engineering, makes the F5 loop meaningful,
and grounds answers in tool results exactly as Claude Code does.

## Decisions (locked during brainstorming + grill-me)

1. **Brain↔agent boundary (B):** the brain is exposed as tools that return *structured*
   results; the model synthesizes prose. Not an opaque "answer" tool, not raw primitives.
2. **One bundled tool:** `search_memory(query, limit?)` returns
   `{ intent, acceptedFacts[], gapFacts[], chunks[] }` — it runs `questionIntent` +
   `loadFacts` + `loadGapFacts` + `retrieveRelevantChunks` internally (intent classified
   inside the tool). Plus `add_memory_note` (class `write_internal`) for writes.
3. **Fact model scope:** port `semantic_facts` + its full lifecycle + extraction. **Defer**
   `entities` / `relationships` / `entity_aliases` — the answer path never reads them
   (verified: `answer.ts` reads only `source_records`, `memory_chunks`, `semantic_facts`).
4. **Extraction:** faithful **two-phase batch** — `npm run ingest` then `npm run refresh`.
   Port `extraction_runs` caching; the LLM extractor routes through the gateway (Anthropic).
   The extractor needs a **concrete candidate Zod schema** (not `z.array(z.unknown())`) so
   Anthropic structured output populates it (the `args:{}` lesson from F5).
5. **Synthesis contract:** the memory-run system prompt requires the **four-section format**
   (Answer / Evidence / Gaps / Next Action), **strict citation grounding** (cite only
   citation ids present in the tool result; never invent), **fact-first / chunk-fallback**,
   **always surface gaps**, **refuse on empty** ("no relevant memory" / "no indexed memory
   yet"). A cheap deterministic **post-check** drops/flags any cited id not in the tool
   result.
6. **No parity gate.** We build the ported system, test the mechanical pieces, run it on
   real data, and ship if it works. No SQLite-vs-Postgres parity harness, no elaborate eval.
7. **Embeddings = pgvector(1536), pluggable provider.** Real OpenAI `text-embedding-3-small`
   when `OPENAI_API_KEY` is set, else a deterministic 1536-dim hash fallback. Same column;
   set the key later to upgrade with no migration.
8. **Permissions (ADR-0001) = carry + filter, single default actor for v1.** Build the
   `source_permissions` column and filter-before-retrieval in SQL; seed one default actor
   with read on all imported sources. Multi-user management UI deferred.

### What we port vs. don't

- **Port:** `chunk` (chunkText/chunkCsv + citations), `ingest` (sources+chunks+embeddings,
  dedup), `embed`→pgvector, `refresh`→`semantic_facts` lifecycle (extraction cache,
  confidence accept at 0.75, `markConflictsAndStaleFacts`, `restoreStaleFacts`), retrieval
  (`retrieveRelevantChunks` hybrid cosine∧lexical) + ranking (`rankRows`, `factIntentScore`,
  `questionIntent`, `loadFacts`, `loadGapFacts`).
- **Don't port:** deterministic prose templates (model synthesizes), `entities`/
  `relationships`/`entity_aliases` graph (unread by the answer), parity harness.
- **Reuse:** `src/extractors/{llm,csv}.ts` (already gateway-routed), `src/lib/harness.ts`
  (+ `instructions` param), gateway, ledger, `/chat`, `/runs/[id]`.

## Module structure

```
src/lib/memory/chunk.ts        port chunkText/chunkCsv + citationForChunk
src/lib/memory/embed.ts        embedText(): pgvector(1536), gateway-or-hash fallback
src/lib/memory/ingest.ts       ingestFolder -> source_records + memory_chunks
src/lib/memory/extract.ts      refresh.ts port -> semantic_facts (cache, accept, conflict/stale)
src/lib/memory/retrieve.ts     searchMemory() -> { intent, acceptedFacts, gapFacts, chunks }
src/lib/memory/notes.ts        addMemoryNote() write/correct path
src/lib/memory/answer-config.ts MEMORY_INSTRUCTIONS + memoryRegistry()
src/lib/tools/search-memory.ts  search_memory tool (class read)
src/lib/tools/add-memory-note.ts add_memory_note tool (class write_internal)
src/migrations/003_memory.sql   source_records, memory_chunks, semantic_facts, extraction_runs, source_permissions
scripts/ingest.ts               npm run ingest <dir>
scripts/refresh.ts              npm run refresh
```

## Components

### Schema (migration 003)

- `source_records`: id, source_id (unique slug), path, title, source_type, content_hash,
  raw_text, timestamps.
- `memory_chunks`: id, source_record_id FK CASCADE, chunk_index, text, chunk_hash,
  `embedding vector(1536)`, citation, timestamps; UNIQUE(source_record_id, chunk_index);
  hnsw cosine index.
- `semantic_facts`: id, subject, predicate, object, source_record_id FK, source_chunk_id FK,
  confidence REAL, status TEXT CHECK in ('candidate','accepted','conflicted','stale'),
  created_at; index on (source_record_id, status).
- `extraction_runs`: id, source_chunk_id FK, cache_key UNIQUE, chunk_hash, extractor_type,
  extractor_version, prompt_hash, schema_version, model_name, raw_output,
  parsed_candidates_json, status, error, created_at.
- `source_permissions`: principal_type, principal_id, source_id, access, UNIQUE(triple).

### Embeddings (`embed.ts`)

`embedText(text): Promise<number[1536]>`. `OPENAI_API_KEY` set → `callModel({kind:"embed",
model:"text-embedding-3-small"})` (logged in `model_calls`); else deterministic hash into
1536 dims (legacy approach widened), L2-normalized, offline, reproducible.
`usesRealEmbeddings()` surfaces the active path.

### Ingestion (`ingest.ts` + `scripts/ingest.ts`)

Port `ingestFolder`: walk .md/.txt/.csv/.eml → `chunk` → `embedText` per chunk → upsert
`source_records` (dedup by content_hash, preserve source_id) → replace `memory_chunks` with
citations (`source-id#chunk-N`, CSV `#row-N`, 1-indexed). Per-file skip reasons reported; no
orphan rows; seed default-actor `source_permissions` per source.

### Extraction (`extract.ts` + `scripts/refresh.ts`)

Port `refreshMemory` for `semantic_facts` only: load chunks → per chunk run extractor
(`createCsvExtractor` for csv, else `createLlmExtractor` via gateway) → cache by `cache_key`
in `extraction_runs` (reuse on hit) → rebuild `semantic_facts` from `semantic_fact`
candidates (confidence ≥ 0.75 → `accepted`, else `candidate`; dedup) → `restoreStaleFacts`
(prior accepted facts that vanish become `stale`) → `markConflictsAndStaleFacts`
(same subject+predicate, >1 distinct object → `conflicted`; orphaned chunk → `stale`).
Entity/relationship candidate kinds are ignored in v1. The LLM extractor gets a concrete
candidate Zod schema so Anthropic structured output populates it.

### Retrieval (`retrieve.ts`) — the bundled tool's engine, ADR-0001 enforced

`searchMemory(db, { query, actor, limit? })` → `{ intent, acceptedFacts, gapFacts, chunks }`:
1. `intent = questionIntent(query)` (ported regex classifier).
2. Resolve actor's allowed source_ids (default-deny).
3. `acceptedFacts` = `loadFacts` (semantic_facts status='accepted', permission-filtered in
   SQL, ranked by `rankRows`/`factIntentScore`, ≤6).
4. `gapFacts` = `loadGapFacts` (candidate/conflicted/stale, ≤6).
5. `chunks` = `retrieveRelevantChunks` — pgvector cosine **gated by lexical match**,
   permission-filtered, top-3, each with citation.
Permission filter is applied in the SQL WHERE before ranking — a restricted source never
surfaces even if it would rank higher.

### `search_memory` tool + agentic answer

- `search_memory` (class `read`): args `{ query, limit? }` → returns the bundle above;
  recorded as a tool_call in the ledger.
- `answer-config.ts`: `MEMORY_INSTRUCTIONS` (the Q5 contract) + `memoryRegistry()` =
  `createToolRegistry([searchMemoryTool])`.
- **Harness change:** `RunAgentInput` gains optional `instructions?: string`;
  `buildAgentSystemPrompt(tools, instructions?)` appends it. Echo run unchanged; memory run
  passes `MEMORY_INSTRUCTIONS`.
- **Route:** `/api/agent` runs the memory config by default (`memoryRegistry()`,
  `allowedClasses:{read}`, `instructions: MEMORY_INSTRUCTIONS`), replacing the echo
  placeholder. Existing `/chat` + `/runs/[id]` render unchanged. A post-check drops any
  cited id not present in the tool result.

### Memory add/correct (`notes.ts` + `add_memory_note`)

`addMemoryNote(db, { title, text, actor })` writes a `source_record` (type `note`) + chunk(s)
(embedded, cited), grants the actor read, returns source_id — retrievable immediately.
Correction = add a superseding note (no destructive edit in v1). Exposed as `add_memory_note`
(class `write_internal`).

## Data flow

```
npm run ingest ./notes -> source_records + memory_chunks (+embeddings, +perms)
npm run refresh         -> extractor per chunk (cached) -> semantic_facts (accept/conflict/stale)

POST /api/agent { question }   (memory config)
  -> runAgent(allowed={read}, tools={search_memory}, instructions=MEMORY_INSTRUCTIONS)
       loop: callModel(decision)
         -> search_memory(query) -> retrieve.searchMemory (permission-filtered)
              -> { intent, acceptedFacts, gapFacts, chunks } back into the loop (logged)
         -> model writes the 4-section cited answer; post-check validates citations
  -> { runId, answer }
GET /runs/[id] -> existing inspector renders the trace
```

## Error handling

- Empty allowed-set / no matches → tool returns empty bundle → model answers
  "no relevant memory" (not a failure).
- Embedding provider error (real path) → `ModelGatewayError`, run fails, inspectable.
- Extraction per-chunk failure → recorded in `extraction_runs` with status='failed'; refresh
  continues; reported in the run summary.
- Ingest per-file failure → reason recorded, reported, no orphan rows, no silent skips.

## Testing (lean — no parity, no elaborate eval)

Live Postgres, reset + migrate in `beforeEach`, mock provider where a model is involved.
- `memory-ingest`: writes records/chunks with citations + 1536-dim embedding; CSV `#row-N`;
  dedup by content_hash; per-file skip reasons; no orphans.
- `memory-embed`: hash fallback deterministic, length-1536, L2-norm ~1; `usesRealEmbeddings()`.
- `memory-extract`: extraction populates `semantic_facts` (accept vs candidate at 0.75);
  conflict detection (same subject+predicate, 2 objects → conflicted); stale on orphaned
  chunk; cache reuse on unchanged chunk.
- `memory-retrieve`: bundle shape; permission filter — **restricted source never surfaces**
  even when it lexically/semantically matches; empty allowed-set → empty.
- `search-memory-tool`: returns the bundle, logs a tool_call.
- `memory-answer`: with a mock provider returning a canned cited answer, assert the
  post-check passes valid citations and flags an invented one; refuse-on-empty path.
Real-run validation (ingest a real corpus, ask in `/chat`, eyeball the cited answer) is the
final check.

## Deferred (YAGNI)

`entities`/`relationships`/`entity_aliases` graph; deterministic prose templates; parity
harness; multi-user permission UI; in-app upload UI (A4.2); live Drive/Gmail/Notion sync.

## Acceptance criteria

- [ ] `npm run ingest <dir>` loads notes as source_records + memory_chunks with pgvector
      embeddings + citations; per-file status reported.
- [ ] `npm run refresh` populates `semantic_facts` with accept/candidate split, conflict and
      stale marking, and reuses cached extractions on unchanged chunks.
- [ ] `search_memory` returns `{ intent, acceptedFacts, gapFacts, chunks }`,
      permission-filtered, cited, logged as a tool call.
- [ ] A restricted source never surfaces to an actor without access (SQL-level filter).
- [ ] Asking in `/chat` runs the harness, calls `search_memory`, and renders a 4-section
      cited answer with gaps; cited ids are validated against the tool result; "no relevant
      memory" when empty.
- [ ] `add_memory_note` writes a retrievable note; a correction note supersedes prior content.
- [ ] The full run appears in the run inspector.

## Maps to original plan slices

- A1 → migration 003 + `chunk.ts`/`ingest.ts` + `scripts/ingest.ts`.
- A2 → `embed.ts` (pluggable 1536) + `retrieve.ts` (permission-filtered hybrid pgvector).
- A3 → **reframed**: faithful port of retrieval/ranking/fact-lifecycle behind `search_memory`
  + agentic synthesis with citation post-check. No `answer.ts` prose port, **no parity gate**.
- (extraction) → `extract.ts` + `scripts/refresh.ts` (the `refresh.ts` port; `semantic_facts` only).
- A5 → `search-memory.ts` registered into the harness — the center of A.
- A6 → reuse `/chat` + memory run config.
- A7 → `notes.ts` + `add_memory_note` — folded in.
- A4 (import UI), entity/relationship graph, prose templates → deferred.
