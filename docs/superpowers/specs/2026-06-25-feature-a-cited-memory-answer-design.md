# Feature A (reframed) — Cited Sourcing Memory Answer: Design

Date: 2026-06-25
Status: Approved for implementation
Feature: A (Cited Sourcing Memory Answer) from
`docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`
Blocked by: F2 (Postgres+pgvector), F3 (gateway embeddings), F5 (agent harness) — F5 in
review on PR #7.
Branch: `feat/a-cited-memory-answer` (off `feat/f5-agent-harness`; rebase onto `main`
after F5 merges).

## Purpose

Let a Sourcing Director ask a question in chat and get a **cited answer with knowledge
gaps**, synthesized from imported sourcing notes held in Postgres/pgvector. This is the
first tier-1 measurable outcome: an answer a director can verify against sources, fully
traced in the Run Ledger.

## The reframe (why this differs from the original A1–A7 plan)

The original plan ported the legacy SQLite engine (`src/answer.ts`, ~420 LOC of regex
intent classification + deterministic synthesis) and gated it behind a strict
SQLite-vs-Postgres parity test, including the semantic fact graph
(`entities`/`relationships`/`semantic_facts`/`extraction_runs`).

A systematic read of the Claude Code production agent (leaked source) plus the internal
fact that we just built the F5 harness reshaped this:

- **Claude Code grounds answers via agent-callable search tools + tool results in the
  transcript, not a precomputed RAG monolith** (`memdir/findRelevantMemories.ts`,
  `query.ts` tool_result handling). It has no `answer.ts`-style retrieve-and-synthesize
  function.
- Porting `answer.ts` as-is would create a non-agentic path that **bypasses the F5
  harness** — incoherent with what we just shipped.

Therefore Feature A is reframed:

1. **Memory is a tool the agent calls** (`search_memory`), not a monolithic function.
2. **The model synthesizes the cited answer** (intent, gaps, refusal) from retrieved
   chunks — `answer.ts`'s deterministic synthesis is NOT ported.
3. **pgvector retrieval is kept** — semantic recall over notes is our legitimate
   difference from Claude Code's grep-over-code (it greps exact strings; we need
   semantic recall over prose).
4. **Strict parity → an answer eval.** Non-deterministic synthesis can't be diffed
   against the old engine; instead we assert structural properties (every claim cites a
   retrieved chunk, gaps surface, refusal on empty, no permission leak).
5. **The semantic fact graph is deferred** — answer from retrieved chunks; "knowledge
   gaps" are agent-identified from retrieval coverage.
6. **Memory add/correct is folded into A** so the learn-loop is closeable from the first
   feature (Claude Code wires feedback early, not as an end-stage add-on).

## Decisions (locked during brainstorming)

- **Scope = the cited-answer core**: ingest + pgvector retrieval + `search_memory` tool +
  agentic answer in chat + memory add/correct. Import UI (A4.2) and the fact graph are
  out.
- **Embeddings = pgvector(1536) with a pluggable provider.** The column is `vector(1536)`.
  Generation goes through the Model Gateway: real OpenAI `text-embedding-3-small` when
  `OPENAI_API_KEY` is set, else a deterministic 1536-dim hash fallback (offline,
  reproducible, weak semantics). Same column either way — setting the key later upgrades
  to real semantic search with no migration.
- **Permissions (ADR-0001) = carry + filter, single default actor for v1.** The
  `source_permissions` column and filter-before-retrieval in SQL are built; a default
  actor seeded with read access to all imported sources. Multi-user permission
  management UI is deferred.
- **Ingestion = CLI for v1.** `npm run ingest <dir>` ports the legacy `ingestFolder` to
  Postgres. In-app upload UI deferred to A4.
- **Synthesis is agentic.** No port of `answer.ts`; the run's system prompt instructs the
  model to answer only from retrieved chunks, cite every claim, list gaps, and refuse
  when retrieval is empty.

## Module structure

Greenfield unless marked. The legacy `src/` SQLite engine is kept untouched as the eval
oracle and chunk/heuristic reference.

```
src/lib/memory/schema.ts        SourceRecord, MemoryChunk, RetrievedChunk types
src/lib/memory/embed.ts         embedText(text): Promise<number[]> — gateway or hash fallback
src/lib/memory/chunk.ts         port of legacy chunkText/chunkCsv + citation construction
src/lib/memory/ingest.ts        ingestFolder -> Postgres (source_records + memory_chunks)
src/lib/memory/retrieve.ts      searchMemory() — pgvector cosine + pre-retrieval permission filter
src/lib/memory/notes.ts         addMemoryNote() write path
src/lib/tools/search-memory.ts  search_memory tool (class read)
src/lib/tools/add-memory-note.ts add_memory_note tool (class write_internal)
src/lib/memory/answer-config.ts the memory-run system prompt + allowed tool set
src/migrations/003_memory.sql   source_records + memory_chunks + source_permissions
scripts/ingest.ts               CLI entry: npm run ingest <dir>
REUSE: src/lib/model-gateway.ts (callModel embed), src/lib/harness.ts (runAgent),
       src/lib/ledger.ts, src/app/chat (ChatClient), src/app/runs/[id]
```

## Components

### Schema (migration 003)

```sql
CREATE TABLE source_records (
  id            BIGSERIAL PRIMARY KEY,
  source_id     TEXT NOT NULL UNIQUE,           -- deterministic slug
  path          TEXT,
  title         TEXT NOT NULL,
  source_type   TEXT NOT NULL,                  -- markdown | text | csv | email
  content_hash  TEXT NOT NULL,                  -- sha256(raw_text), dedupe key
  raw_text      TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memory_chunks (
  id               BIGSERIAL PRIMARY KEY,
  source_record_id BIGINT NOT NULL REFERENCES source_records(id) ON DELETE CASCADE,
  chunk_index      INTEGER NOT NULL,
  text             TEXT NOT NULL,
  chunk_hash       TEXT NOT NULL,
  embedding        vector(1536),                -- pgvector; pluggable provider
  citation         TEXT NOT NULL,               -- source-id#chunk-N / #row-N
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_record_id, chunk_index)
);
CREATE INDEX memory_chunks_embedding_idx ON memory_chunks
  USING hnsw (embedding vector_cosine_ops);

CREATE TABLE source_permissions (
  id             BIGSERIAL PRIMARY KEY,
  principal_type TEXT NOT NULL,                 -- user | oauth_client | test_client
  principal_id   TEXT NOT NULL,
  source_id      TEXT NOT NULL,
  access         TEXT NOT NULL DEFAULT 'read',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (principal_type, principal_id, source_id)
);
```

### Embeddings (`embed.ts`)

`embedText(text: string): Promise<number[]>` returns a length-1536 vector.
- If `process.env.OPENAI_API_KEY?.trim()`: `callModel({ kind: "embed",
  model: "text-embedding-3-small" })` (logged in `model_calls`).
- Else: deterministic hash — tokenize, sha256 each token, index mod 1536, increment,
  L2-normalize (the legacy 64-dim approach widened to 1536). No network, reproducible.
A `usesRealEmbeddings(): boolean` helper surfaces which path is active (for the import
report and tests).

### Ingestion (`ingest.ts` + `scripts/ingest.ts`)

Port of `src/ingest.ts`: walk supported files (.md/.txt/.csv/.eml) → `chunk` →
`embedText` each chunk → upsert `source_records` (preserve source_id on conflict, dedupe
by content_hash) → replace `memory_chunks` with citations
(`citationForChunk` → `source-id#chunk-N`, 1-indexed; CSV → `#row-N`). Returns
`{ processed, skipped, skippedFiles[] }` with per-file reasons (no silent skips). Seeds
the default actor's `source_permissions` for every ingested source.

### Retrieval (`retrieve.ts`) — ADR-0001 enforced

```ts
searchMemory(db, {
  query: string;
  actor: { principalType: string; principalId: string };
  limit?: number;            // default 6
}): Promise<RetrievedChunk[]>   // { text, citation, sourceId, score }
```
1. Resolve the actor's allowed `source_id`s from `source_permissions` (default-deny).
2. Embed the query via `embedText`.
3. **Single SQL query** that filters to allowed sources in the WHERE clause AND ranks by
   `embedding <=> $queryVec` (cosine). Permission filter is applied before ranking — a
   restricted source can never surface even if it would rank higher.
4. Return top-K chunks with citations. Empty allowed-set → empty result (not an error).

### `search_memory` tool + agentic answer

- `search_memory` (class `read`): `argsSchema { query: string, limit?: number }`;
  `execute` calls `searchMemory` with the run's actor and returns
  `{ chunks: [{ citation, text, sourceId }] }`. Recorded as a tool_call in the ledger.
- **Harness change:** `RunAgentInput` gains an optional `instructions?: string`.
  `buildAgentSystemPrompt(tools, instructions?)` appends it after the tool catalog, so a
  run can carry task guidance without forking the harness. The echo run passes nothing
  (unchanged behavior); the memory run passes the memory instructions below.
- `answer-config.ts` exports `MEMORY_INSTRUCTIONS` — *"Answer only from search_memory
  results. Cite every claim with its citation id in brackets. If retrieved coverage is
  thin or conflicting, list it under Knowledge Gaps. If search_memory returns nothing,
  say there is no relevant memory. Never invent sources."* — and
  `memoryRegistry()` = `createToolRegistry([searchMemoryTool])`.
- **Route:** `/api/agent` runs the memory config by default
  (`registry: memoryRegistry()`, `allowedClasses: {read}`, `instructions:
  MEMORY_INSTRUCTIONS`), replacing the echo placeholder. The existing `/chat` renders the
  cited answer + the run-inspector link unchanged.

### Memory add/correct (`notes.ts` + `add_memory_note` tool)

`addMemoryNote(db, { title, text, actor })` writes a `source_record` (source_type
`note`) + chunk(s) (embedded, cited), grants the actor read, and returns the source_id —
immediately retrievable. **Correction = add a superseding note** (no destructive edit in
v1). Exposed as `add_memory_note` tool (class `write_internal`) so an agent run can write
back, and callable directly from a future memory page.

## Data flow

```
npm run ingest ./notes
  -> ingestFolder -> per file: chunk -> embedText -> source_records + memory_chunks (+perms)

POST /api/agent { question }   (memory run config)
  -> runAgent(allowed={read}, tools={search_memory})
       loop: callModel(generate_object decision)
         -> search_memory(query) -> retrieve.searchMemory (permission-filtered pgvector)
              -> cited chunks back into the loop (tool_call logged)
         -> model writes final cited answer + gaps
  -> { runId, answer }
GET /runs/[id] -> existing inspector renders the trace (search_memory tool calls + answer)
```

## Error handling

- Empty allowed-set or no matching chunks → the tool returns an empty result; the model
  answers "no relevant memory" (not a run failure).
- Embedding provider error (real path) → surfaces as a `ModelGatewayError`; the run fails
  and is inspectable (consistent with F5).
- Ingest per-file failures → recorded with a reason and reported; never silently skipped;
  never leaves orphan source/chunk rows.

## Testing

Live Postgres, same harness as F3/F4/F5. Reset tables + run migrations in `beforeEach`.

- `tests/memory-ingest.test.ts`: ingest writes source_records + chunks with citations and
  a 1536-dim embedding; CSV → `#row-N`; per-file skip reasons; no orphan rows.
- `tests/memory-embed.test.ts`: hash fallback is deterministic and length-1536, L2-norm ~1;
  `usesRealEmbeddings()` reflects env.
- `tests/memory-retrieve.test.ts`: pgvector ranking returns cited chunks; **a restricted
  source never surfaces even when it lexically/semantically matches** (ported from
  `read-service.test.ts`); empty allowed-set → empty.
- `tests/search-memory-tool.test.ts`: tool returns cited chunks and logs a tool_call.
- `tests/memory-answer-eval.test.ts` (the parity replacement): seed corpus + fixed
  question set, mock provider returning canned cited answers; assert structural
  properties — every cited id exists in the retrieved set, gaps surface on thin coverage,
  "no relevant memory" on empty, restricted content never leaks. A separate opt-in live
  run (real Anthropic) is manual, not in CI.
- The legacy SQLite suite (`tests/answer.test.ts`, `read-service.test.ts`, etc.) stays
  green, untouched, as the reference oracle.

## Deferred (YAGNI)

- Semantic fact graph: `entities`, `relationships`, `semantic_facts`, `extraction_runs`
  and the LLM extraction pipeline.
- Multi-user permission management UI.
- In-app file upload UI (A4.2) — CLI ingest covers v1.
- Live Drive/Gmail/Notion sync.

## Acceptance criteria

- [ ] `npm run ingest <dir>` loads notes into Postgres as source_records + memory_chunks
      with pgvector embeddings and citations; per-file status reported.
- [ ] `search_memory` returns permission-filtered, cited chunks and logs a tool call.
- [ ] A restricted source never surfaces to an actor without access (SQL-level filter).
- [ ] Asking a question in `/chat` runs the harness, calls `search_memory`, and renders a
      cited answer with knowledge gaps; "no relevant memory" when retrieval is empty.
- [ ] `add_memory_note` writes a retrievable note; a correction note supersedes prior
      content in future answers.
- [ ] The full run (search_memory tool calls, model calls, answer, status) appears in the
      run inspector.
- [ ] Answer eval passes on the seed question set; legacy SQLite suite stays green.

## Maps to original plan slices

- A1 (schema+ingest) → migration 003 + `ingest.ts`/`chunk.ts` + CLI.
- A2 (embeddings + retrieval) → `embed.ts` (pluggable 1536) + `retrieve.ts` (permission-filtered pgvector).
- A3 (answer + parity) → **reframed**: agentic answer via prompt + answer eval (no `answer.ts` port, no strict parity).
- A5 (search_memory tool) → `search-memory.ts`, registered into the harness — **the center of A**.
- A6 (Research Chat from memory) → reuse existing `/chat` + memory run config.
- A7 (add/correct) → `notes.ts` + `add_memory_note` — **folded into A**.
- A4 (import UI), fact graph → **deferred**.
