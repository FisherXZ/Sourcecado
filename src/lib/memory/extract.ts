/**
 * extract.ts — async Postgres port of the semantic_facts lifecycle from src/refresh.ts.
 *
 * Scope: semantic_facts ONLY (no entities / relationships / entity_aliases — those tables
 * do not exist in this stack).  Entity and relationship candidates from extractors are
 * silently skipped.
 */

import type postgres from "postgres";
import { createCsvExtractor } from "../../extractors/csv.js";
import { createLlmExtractor, LLM_SCHEMA_VERSION } from "../../extractors/llm.js";
import type { ExtractionInput, Extractor } from "../../extractors/types.js";
import type { ExtractedCandidate, SourceType } from "../../types.js";
import { sha256 } from "./chunk";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACCEPTED_CONFIDENCE_THRESHOLD = 0.75;
const DEFAULT_PROMPT_HASH = "none";
const DEFAULT_SCHEMA_VERSION = "1";
const DEFAULT_MODEL_NAME = "local";

// ---------------------------------------------------------------------------
// Public interfaces
// ---------------------------------------------------------------------------

export interface RefreshMemoryOptions {
  /** Per-source-type extractor override, checked before selectExtractor. */
  extractorsBySourceType?: Partial<Record<SourceType, Extractor>>;
  /** Custom extractor selector, used when no per-type override matches. */
  selectExtractor?: (input: ExtractionInput) => Extractor;
  /** Override metadata (promptHash / schemaVersion / modelName) per extractor type. */
  metadataByExtractorType?: Partial<Record<string, Partial<ExtractionMetadata>>>;
}

export interface RefreshMemoryResult {
  chunksProcessed: number;
  extracted: number;
  reused: number;
  failed: number;
}

// ---------------------------------------------------------------------------
// Private types
// ---------------------------------------------------------------------------

interface ExtractionMetadata {
  promptHash: string;
  schemaVersion: string;
  modelName: string;
}

// Postgres BIGSERIAL IDs come back as strings from postgres.js
interface SourceChunkRow {
  chunk_id: string;
  source_record_id: string;
  source_path: string;
  source_type: SourceType;
  text: string;
  chunk_hash: string;
}

interface ExtractionRunRow {
  parsed_candidates_json: unknown | null;
}

interface ExistingFactSnapshot {
  subject: string;
  predicate: string;
  object: string;
  source_record_id: string | null;
  source_chunk_id: string | null;
  confidence: number;
}

type Sql = postgres.Sql;

// ---------------------------------------------------------------------------
// Public: refreshMemory
// ---------------------------------------------------------------------------

export async function refreshMemory(
  db: Sql,
  options: RefreshMemoryOptions = {}
): Promise<RefreshMemoryResult> {
  const chunks = await loadSourceChunks(db);
  const result: RefreshMemoryResult = {
    chunksProcessed: chunks.length,
    extracted: 0,
    reused: 0,
    failed: 0,
  };

  const candidateBatches: Array<{ chunk: SourceChunkRow; candidates: ExtractedCandidate[] }> = [];

  for (const chunk of chunks) {
    const input = toExtractionInput(chunk);
    const extractor = pickExtractor(input, options);
    const metadata = metadataForExtractor(extractor, options);
    const cacheKey = buildCacheKey(chunk.chunk_hash, extractor, metadata);
    const cached = await loadCachedRun(db, cacheKey);

    if (cached && cached.parsed_candidates_json !== null && cached.parsed_candidates_json !== undefined) {
      const cachedCandidates = Array.isArray(cached.parsed_candidates_json)
        ? (cached.parsed_candidates_json as ExtractedCandidate[])
        : [];
      candidateBatches.push({ chunk, candidates: cachedCandidates });
      await db`UPDATE extraction_runs SET source_chunk_id = ${chunk.chunk_id} WHERE cache_key = ${cacheKey}`;
      result.reused += 1;
      continue;
    }

    try {
      const candidates = await extractor.extract(input);
      await recordExtractionRun(db, { chunk, extractor, metadata, cacheKey, candidates, status: "succeeded", error: null });
      candidateBatches.push({ chunk, candidates });
      result.extracted += 1;
    } catch (error) {
      await recordExtractionRun(db, { chunk, extractor, metadata, cacheKey, candidates: [], status: "failed", error: errorMessage(error) });
      result.failed += 1;
    }
  }

  await rebuildDerivedMemory(db, candidateBatches);
  await markConflictsAndStaleFacts(db);

  return result;
}

// ---------------------------------------------------------------------------
// Public: normalizeMemoryKey
// ---------------------------------------------------------------------------

export function normalizeMemoryKey(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/['"]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\s/g, "-");
}

// ---------------------------------------------------------------------------
// Public: markConflictsAndStaleFacts
// ---------------------------------------------------------------------------

export async function markConflictsAndStaleFacts(db: Sql): Promise<void> {
  // Find non-candidate subject+predicate groups with more than one distinct object
  const groups = await db<{ subject_key: string; predicate_key: string }[]>`
    SELECT subject_key, predicate_key
    FROM (
      SELECT
        lower(trim(subject))   AS subject_key,
        lower(trim(predicate)) AS predicate_key,
        count(distinct lower(trim(object))) AS object_count
      FROM semantic_facts
      WHERE status != 'candidate'
      GROUP BY lower(trim(subject)), lower(trim(predicate))
    ) g
    WHERE g.object_count > 1
  `;

  for (const group of groups) {
    await db`
      UPDATE semantic_facts
      SET status = 'conflicted'
      WHERE lower(trim(subject))   = ${group.subject_key}
        AND lower(trim(predicate)) = ${group.predicate_key}
        AND status != 'candidate'
    `;
  }

  // Mark facts stale when their source_chunk_id points to a missing chunk.
  // NOTE: under the `source_chunk_id … ON DELETE SET NULL` FK, deleting a chunk
  // nulls this column, so `source_chunk_id IS NOT NULL AND NOT EXISTS(chunk)` can
  // never fire through a normal application DELETE. This branch is UNREACHABLE in
  // normal operation and exists as defensive-only SQL — the only way to reach it
  // is by bypassing the FK (DDL / session_replication_role); the covering test
  // manufactures such an orphan via DDL. Task 5: do not treat this as live orphan
  // protection.
  await db`
    UPDATE semantic_facts
    SET status = 'stale'
    WHERE source_chunk_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM memory_chunks WHERE memory_chunks.id = semantic_facts.source_chunk_id
      )
  `;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

async function loadSourceChunks(db: Sql): Promise<SourceChunkRow[]> {
  return db<SourceChunkRow[]>`
    SELECT
      memory_chunks.id          AS chunk_id,
      source_records.id         AS source_record_id,
      source_records.path       AS source_path,
      source_records.source_type AS source_type,
      memory_chunks.text        AS text,
      memory_chunks.chunk_hash  AS chunk_hash
    FROM memory_chunks
    JOIN source_records ON source_records.id = memory_chunks.source_record_id
    ORDER BY source_records.id, memory_chunks.chunk_index
  `;
}

function toExtractionInput(chunk: SourceChunkRow): ExtractionInput {
  return {
    sourceId: chunk.source_record_id,
    sourcePath: chunk.source_path,
    sourceType: chunk.source_type,
    content: chunk.text,
  };
}

function pickExtractor(input: ExtractionInput, options: RefreshMemoryOptions): Extractor {
  const injected = options.extractorsBySourceType?.[input.sourceType];
  if (injected) return injected;
  if (options.selectExtractor) return options.selectExtractor(input);
  if (input.sourceType === "csv") return createCsvExtractor();
  return createLlmExtractor();
}

function metadataForExtractor(extractor: Extractor, options: RefreshMemoryOptions): ExtractionMetadata {
  const provided = options.metadataByExtractorType?.[extractor.type] ?? {};
  const generationProvider = process.env.SOURCECADO_GENERATION_PROVIDER?.trim() || "deepseek";
  const generationModel =
    process.env.SOURCECADO_GENERATION_MODEL?.trim() ||
    (generationProvider === "anthropic" ? "claude-sonnet-4-6" : "deepseek-chat");
  const defaults =
    extractor.type === "llm"
      ? {
          promptHash: sha256("sourcyavo-llm-extractor-v1"),
          schemaVersion: LLM_SCHEMA_VERSION,
          modelName: generationModel,
        }
      : {
          promptHash: DEFAULT_PROMPT_HASH,
          schemaVersion: DEFAULT_SCHEMA_VERSION,
          modelName: extractor.type === "mock" ? "mock" : DEFAULT_MODEL_NAME,
        };

  return {
    promptHash: provided.promptHash ?? extractor.promptHash ?? defaults.promptHash,
    schemaVersion: provided.schemaVersion ?? extractor.schemaVersion ?? defaults.schemaVersion,
    modelName: provided.modelName ?? extractor.modelName ?? defaults.modelName,
  };
}

function buildCacheKey(chunkHash: string, extractor: Extractor, metadata: ExtractionMetadata): string {
  return [
    chunkHash,
    extractor.type,
    extractor.version,
    metadata.promptHash,
    metadata.schemaVersion,
    metadata.modelName,
  ].join(":");
}

async function loadCachedRun(db: Sql, cacheKey: string): Promise<ExtractionRunRow | undefined> {
  const [row] = await db<ExtractionRunRow[]>`
    SELECT parsed_candidates_json
    FROM extraction_runs
    WHERE cache_key = ${cacheKey} AND status = 'succeeded'
  `;
  return row;
}

async function recordExtractionRun(
  db: Sql,
  run: {
    chunk: SourceChunkRow;
    extractor: Extractor;
    metadata: ExtractionMetadata;
    cacheKey: string;
    candidates: ExtractedCandidate[];
    status: "succeeded" | "failed";
    error: string | null;
  }
): Promise<void> {
  const rawOutput = JSON.stringify(run.candidates);
  const parsedCandidates = run.status === "succeeded" ? run.candidates : [];

  await db`
    INSERT INTO extraction_runs (
      source_chunk_id, cache_key, chunk_hash, extractor_type, extractor_version,
      prompt_hash, schema_version, model_name, raw_output, parsed_candidates_json, status, error
    ) VALUES (
      ${run.chunk.chunk_id},
      ${run.cacheKey},
      ${run.chunk.chunk_hash},
      ${run.extractor.type},
      ${run.extractor.version},
      ${run.metadata.promptHash},
      ${run.metadata.schemaVersion},
      ${run.metadata.modelName},
      ${rawOutput},
      ${db.json(parsedCandidates as postgres.JSONValue)},
      ${run.status},
      ${run.error}
    )
    -- On a repeated failure, this overwrites the prior failure row, so the error
    -- column reflects only the most recent attempt. Acceptable: failed runs are
    -- never cache-hit (loadCachedRun filters status = succeeded), so they re-run.
    ON CONFLICT (cache_key) DO UPDATE SET
      source_chunk_id        = EXCLUDED.source_chunk_id,
      raw_output             = EXCLUDED.raw_output,
      parsed_candidates_json = EXCLUDED.parsed_candidates_json,
      status                 = EXCLUDED.status,
      error                  = EXCLUDED.error
  `;
}

async function rebuildDerivedMemory(
  db: Sql,
  batches: Array<{ chunk: SourceChunkRow; candidates: ExtractedCandidate[] }>
): Promise<void> {
  await db.begin(async (tx) => {
    const previousFacts = await snapshotAcceptedFacts(tx);
    await tx`DELETE FROM semantic_facts`;

    const seenFacts = new Set<string>();

    for (const batch of batches) {
      for (const candidate of batch.candidates) {
        // Only semantic_facts are supported in this stack.
        if (candidate.kind !== "semantic_fact") continue;
        if (candidate.subject && candidate.predicate && candidate.object) {
          await insertSemanticFact(tx, candidate, batch.chunk, seenFacts);
        }
      }
    }

    await restoreStaleFacts(tx, previousFacts, seenFacts);
  });
}

async function snapshotAcceptedFacts(db: Sql): Promise<ExistingFactSnapshot[]> {
  return db<ExistingFactSnapshot[]>`
    SELECT subject, predicate, object, source_record_id, source_chunk_id, confidence
    FROM semantic_facts
    WHERE status = 'accepted'
  `;
}

async function restoreStaleFacts(
  db: Sql,
  previousFacts: ExistingFactSnapshot[],
  seenFacts: Set<string>
): Promise<void> {
  for (const fact of previousFacts) {
    // If the prior accepted fact's chunk was deleted, its snapshot source_chunk_id
    // is null, so this key can never match a fresh-extraction key (which carries a
    // live numeric chunk id) — the fact is therefore always restored as stale, which
    // is the intended behavior.
    const key = [
      normalizeMemoryKey(fact.subject),
      normalizeMemoryKey(fact.predicate),
      normalizeMemoryKey(fact.object),
      fact.source_record_id,
      fact.source_chunk_id,
    ].join(":");

    if (seenFacts.has(key)) continue;

    await db`
      INSERT INTO semantic_facts
        (subject, predicate, object, source_record_id, source_chunk_id, confidence, status)
      VALUES
        (${fact.subject}, ${fact.predicate}, ${fact.object},
         ${fact.source_record_id}, ${fact.source_chunk_id}, ${fact.confidence}, 'stale')
    `;
  }
}

async function insertSemanticFact(
  db: Sql,
  candidate: ExtractedCandidate,
  chunk: SourceChunkRow,
  seen: Set<string>
): Promise<void> {
  const subject = candidate.subject ?? "";
  const predicate = candidate.predicate ?? "";
  const object = candidate.object ?? "";
  const key = [
    normalizeMemoryKey(subject),
    normalizeMemoryKey(predicate),
    normalizeMemoryKey(object),
    chunk.source_record_id,
    chunk.chunk_id,
  ].join(":");

  if (seen.has(key)) return;
  seen.add(key);

  const status = candidate.confidence >= ACCEPTED_CONFIDENCE_THRESHOLD ? "accepted" : "candidate";

  await db`
    INSERT INTO semantic_facts
      (subject, predicate, object, source_record_id, source_chunk_id, confidence, status)
    VALUES
      (${subject.trim()}, ${predicate.trim()}, ${object.trim()},
       ${chunk.source_record_id}, ${chunk.chunk_id}, ${candidate.confidence}, ${status})
  `;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
