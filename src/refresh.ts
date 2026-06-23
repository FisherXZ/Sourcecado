import type { MemoryDatabase } from "./db.js";
import { sha256 } from "./chunk.js";
import type {
  EntityType,
  ExtractedCandidate,
  RelationshipType,
  SourceType
} from "./types.js";
import { createCsvExtractor } from "./extractors/csv.js";
import { createLlmExtractor, LLM_SCHEMA_VERSION } from "./extractors/llm.js";
import type { ExtractionInput, Extractor } from "./extractors/types.js";

const ACCEPTED_CONFIDENCE_THRESHOLD = 0.75;
const DEFAULT_PROMPT_HASH = "none";
const DEFAULT_SCHEMA_VERSION = "1";
const DEFAULT_MODEL_NAME = "local";

export interface RefreshMemoryOptions {
  extractorsBySourceType?: Partial<Record<SourceType, Extractor>>;
  selectExtractor?: (input: ExtractionInput) => Extractor;
  metadataByExtractorType?: Partial<Record<string, Partial<ExtractionMetadata>>>;
}

export interface RefreshMemoryResult {
  chunksProcessed: number;
  extracted: number;
  reused: number;
  failed: number;
}

interface ExtractionMetadata {
  promptHash: string;
  schemaVersion: string;
  modelName: string;
}

interface SourceChunkRow {
  chunk_id: number;
  source_record_id: number;
  source_path: string;
  source_type: SourceType;
  text: string;
  chunk_hash: string;
}

interface ExtractionRunRow {
  parsed_candidates_json: string | null;
}

interface EntityRow {
  id: number;
  type: EntityType;
  name: string;
  canonical_key: string;
}

interface FactGroupRow {
  subject_key: string;
  predicate_key: string;
  object_count: number;
}

interface ExistingFactSnapshot {
  subject: string;
  predicate: string;
  object: string;
  source_record_id: number | null;
  source_chunk_id: number | null;
  confidence: number;
  status: string;
}

export async function refreshMemory(
  db: MemoryDatabase,
  options: RefreshMemoryOptions = {}
): Promise<RefreshMemoryResult> {
  const chunks = loadSourceChunks(db);
  const result: RefreshMemoryResult = {
    chunksProcessed: chunks.length,
    extracted: 0,
    reused: 0,
    failed: 0
  };

  const candidateBatches: Array<{ chunk: SourceChunkRow; candidates: ExtractedCandidate[] }> = [];

  for (const chunk of chunks) {
    const input = toExtractionInput(chunk);
    const extractor = selectExtractor(input, options);
    const metadata = metadataForExtractor(extractor, options);
    const cacheKey = buildCacheKey(chunk.chunk_hash, extractor, metadata);
    const cached = loadCachedRun(db, cacheKey);

    if (cached?.parsed_candidates_json) {
      candidateBatches.push({
        chunk,
        candidates: parseCachedCandidates(cached.parsed_candidates_json)
      });
      db.prepare("update extraction_runs set source_chunk_id = ? where cache_key = ?").run(
        chunk.chunk_id,
        cacheKey
      );
      result.reused += 1;
      continue;
    }

    try {
      const candidates = await extractor.extract(input);
      recordExtractionRun(db, {
        chunk,
        extractor,
        metadata,
        cacheKey,
        candidates,
        status: "succeeded",
        error: null
      });
      candidateBatches.push({ chunk, candidates });
      result.extracted += 1;
    } catch (error) {
      recordExtractionRun(db, {
        chunk,
        extractor,
        metadata,
        cacheKey,
        candidates: [],
        status: "failed",
        error: errorMessage(error)
      });
      result.failed += 1;
    }
  }

  rebuildDerivedMemory(db, candidateBatches);
  markConflictsAndStaleFacts(db);

  return result;
}

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

export function markConflictsAndStaleFacts(db: MemoryDatabase): void {
  const groups = db
    .prepare(
      [
        "select",
        "subject_key, predicate_key, count(distinct object_key) as object_count",
        "from (",
        "select",
        "lower(trim(subject)) as subject_key,",
        "lower(trim(predicate)) as predicate_key,",
        "lower(trim(object)) as object_key",
        "from semantic_facts",
        "where status != 'candidate'",
        ")",
        "group by subject_key, predicate_key",
        "having object_count > 1"
      ].join(" ")
    )
    .all() as FactGroupRow[];

  const markConflict = db.prepare(
    "update semantic_facts set status = 'conflicted' where lower(trim(subject)) = ? and lower(trim(predicate)) = ? and status != 'candidate'"
  );

  for (const group of groups) {
    markConflict.run(group.subject_key, group.predicate_key);
  }

  const markStale = db.prepare(
    [
      "update semantic_facts set status = 'stale'",
      "where source_chunk_id is not null",
      "and not exists (select 1 from memory_chunks where memory_chunks.id = semantic_facts.source_chunk_id)"
    ].join(" ")
  );
  markStale.run();
}

function loadSourceChunks(db: MemoryDatabase): SourceChunkRow[] {
  return db
    .prepare(
      [
        "select",
        "memory_chunks.id as chunk_id,",
        "source_records.id as source_record_id,",
        "source_records.path as source_path,",
        "source_records.source_type as source_type,",
        "memory_chunks.text as text,",
        "memory_chunks.chunk_hash as chunk_hash",
        "from memory_chunks",
        "join source_records on source_records.id = memory_chunks.source_record_id",
        "order by source_records.id, memory_chunks.chunk_index"
      ].join(" ")
    )
    .all() as SourceChunkRow[];
}

function toExtractionInput(chunk: SourceChunkRow): ExtractionInput {
  return {
    sourceId: String(chunk.source_record_id),
    sourcePath: chunk.source_path,
    sourceType: chunk.source_type,
    content: chunk.text
  };
}

function selectExtractor(input: ExtractionInput, options: RefreshMemoryOptions): Extractor {
  const injected = options.extractorsBySourceType?.[input.sourceType];
  if (injected) {
    return injected;
  }
  if (options.selectExtractor) {
    return options.selectExtractor(input);
  }
  if (input.sourceType === "csv") {
    return createCsvExtractor();
  }
  return createLlmExtractor();
}

function metadataForExtractor(
  extractor: Extractor,
  options: RefreshMemoryOptions
): ExtractionMetadata {
  const provided = options.metadataByExtractorType?.[extractor.type] ?? {};
  const defaults =
    extractor.type === "llm"
      ? {
          promptHash: sha256("sourcyavo-llm-extractor-v1"),
          schemaVersion: LLM_SCHEMA_VERSION,
          modelName: process.env.SOURCECADO_GENERATION_MODEL || "deepseek-chat"
        }
      : {
          promptHash: DEFAULT_PROMPT_HASH,
          schemaVersion: DEFAULT_SCHEMA_VERSION,
          modelName: extractor.type === "mock" ? "mock" : DEFAULT_MODEL_NAME
        };

  return {
    promptHash: provided.promptHash ?? extractor.promptHash ?? defaults.promptHash,
    schemaVersion: provided.schemaVersion ?? extractor.schemaVersion ?? defaults.schemaVersion,
    modelName: provided.modelName ?? extractor.modelName ?? defaults.modelName
  };
}

function buildCacheKey(
  chunkHash: string,
  extractor: Extractor,
  metadata: ExtractionMetadata
): string {
  return [
    chunkHash,
    extractor.type,
    extractor.version,
    metadata.promptHash,
    metadata.schemaVersion,
    metadata.modelName
  ].join(":");
}

function loadCachedRun(db: MemoryDatabase, cacheKey: string): ExtractionRunRow | undefined {
  return db
    .prepare(
      "select parsed_candidates_json from extraction_runs where cache_key = ? and status = 'succeeded'"
    )
    .get(cacheKey) as ExtractionRunRow | undefined;
}

function parseCachedCandidates(json: string): ExtractedCandidate[] {
  return JSON.parse(json) as ExtractedCandidate[];
}

function recordExtractionRun(
  db: MemoryDatabase,
  run: {
    chunk: SourceChunkRow;
    extractor: Extractor;
    metadata: ExtractionMetadata;
    cacheKey: string;
    candidates: ExtractedCandidate[];
    status: "succeeded" | "failed";
    error: string | null;
  }
): void {
  const rawOutput = JSON.stringify(run.candidates);
  const parsedCandidatesJson = run.status === "succeeded" ? rawOutput : JSON.stringify([]);

  db.prepare(
    [
      "insert into extraction_runs (",
      "source_chunk_id, cache_key, chunk_hash, extractor_type, extractor_version,",
      "prompt_hash, schema_version, model_name, raw_output, parsed_candidates_json, status, error",
      ") values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      "on conflict(cache_key) do update set",
      "source_chunk_id = excluded.source_chunk_id,",
      "raw_output = excluded.raw_output,",
      "parsed_candidates_json = excluded.parsed_candidates_json,",
      "status = excluded.status,",
      "error = excluded.error"
    ].join(" ")
  ).run(
    run.chunk.chunk_id,
    run.cacheKey,
    run.chunk.chunk_hash,
    run.extractor.type,
    run.extractor.version,
    run.metadata.promptHash,
    run.metadata.schemaVersion,
    run.metadata.modelName,
    rawOutput,
    parsedCandidatesJson,
    run.status,
    run.error
  );
}

function rebuildDerivedMemory(
  db: MemoryDatabase,
  batches: Array<{ chunk: SourceChunkRow; candidates: ExtractedCandidate[] }>
): void {
  db.transaction(() => {
    const previousFacts = snapshotAcceptedFacts(db);
    db.prepare("delete from relationships").run();
    db.prepare("delete from entity_aliases").run();
    db.prepare("delete from entities").run();
    db.prepare("delete from semantic_facts").run();

    const seenFacts = new Set<string>();
    const seenRelationships = new Set<string>();

    for (const batch of batches) {
      for (const candidate of batch.candidates) {
        if (candidate.kind === "entity") {
          if (candidate.subject && candidate.entityType) {
            upsertEntityWithAlias(db, candidate.entityType, candidate.subject);
          }
          continue;
        }

        if (candidate.kind === "relationship") {
          if (candidate.subject && candidate.object && candidate.relationshipType) {
            insertRelationship(db, candidate, batch.chunk, seenRelationships);
          }
          continue;
        }

        if (candidate.kind === "semantic_fact") {
          if (candidate.subject && candidate.predicate && candidate.object) {
            insertSemanticFact(db, candidate, batch.chunk, seenFacts);
          }
        }
      }
    }

    restoreStaleFacts(db, previousFacts, seenFacts);
  })();
}

function upsertEntityWithAlias(db: MemoryDatabase, type: EntityType, name: string): number {
  const aliasKey = aliasKeyFor(type, name);
  const existingAlias = db
    .prepare("select entity_id as id from entity_aliases where alias_key = ?")
    .get(aliasKey) as { id: number } | undefined;
  if (existingAlias) {
    return existingAlias.id;
  }

  const existingEntity = findMergeableEntity(db, type, name);
  if (existingEntity) {
    insertAlias(db, existingEntity.id, type, name);
    return existingEntity.id;
  }

  const canonicalKey = aliasKey;
  db.prepare("insert into entities (type, name, canonical_key) values (?, ?, ?)").run(
    type,
    name.trim(),
    canonicalKey
  );
  const inserted = db
    .prepare("select id from entities where canonical_key = ?")
    .get(canonicalKey) as { id: number } | undefined;
  if (!inserted) {
    throw new Error("Failed to insert entity");
  }
  insertAlias(db, inserted.id, type, name);
  return inserted.id;
}

function findMergeableEntity(
  db: MemoryDatabase,
  type: EntityType,
  name: string
): EntityRow | undefined {
  const canonicalKey = aliasKeyFor(type, name);
  const exact = db
    .prepare("select id, type, name, canonical_key from entities where canonical_key = ?")
    .get(canonicalKey) as EntityRow | undefined;
  if (exact) {
    return exact;
  }

  if (type !== "person") {
    return undefined;
  }

  const tokens = normalizeMemoryKey(name).split("-").filter(Boolean);
  if (tokens.length < 2) {
    return undefined;
  }
  const [firstToken, secondToken] = tokens;
  const lastInitial = secondToken?.[0];

  const candidates = db
    .prepare(
      "select id, type, name, canonical_key from entities where type = ? and canonical_key like ? order by length(name) desc, id"
    )
    .all(type, `${type}:${firstToken}-%`) as EntityRow[];

  return candidates.find((entity) => {
    const existingTokens = entity.canonical_key
      .replace(`${type}:`, "")
      .split("-")
      .filter(Boolean);
    return existingTokens[0] === firstToken && existingTokens[1]?.[0] === lastInitial;
  });
}

function insertAlias(db: MemoryDatabase, entityId: number, type: EntityType, alias: string): void {
  db.prepare(
    "insert or ignore into entity_aliases (entity_id, alias, alias_key) values (?, ?, ?)"
  ).run(entityId, alias.trim(), aliasKeyFor(type, alias));
}

function insertRelationship(
  db: MemoryDatabase,
  candidate: ExtractedCandidate,
  chunk: SourceChunkRow,
  seen: Set<string>
): void {
  const subject = candidate.subject ?? "";
  const object = candidate.object ?? "";
  const relationshipType = candidate.relationshipType as RelationshipType;
  const fromId = upsertEntityWithAlias(db, inferEntityTypeForSubject(db, subject), subject);
  const toId = upsertEntityWithAlias(db, inferEntityTypeForObject(relationshipType), object);
  const key = [
    fromId,
    toId,
    relationshipType,
    chunk.source_record_id,
    chunk.chunk_id,
    candidate.evidenceText
  ].join(":");

  if (seen.has(key)) {
    return;
  }
  seen.add(key);

  db.prepare(
    [
      "insert into relationships",
      "(from_entity_id, to_entity_id, type, source_record_id, source_chunk_id, confidence, note)",
      "values (?, ?, ?, ?, ?, ?, ?)"
    ].join(" ")
  ).run(
    fromId,
    toId,
    relationshipType,
    chunk.source_record_id,
    chunk.chunk_id,
    candidate.confidence,
    candidate.evidenceText
  );
}

function insertSemanticFact(
  db: MemoryDatabase,
  candidate: ExtractedCandidate,
  chunk: SourceChunkRow,
  seen: Set<string>
): void {
  const subject = candidate.subject ?? "";
  const predicate = candidate.predicate ?? "";
  const object = candidate.object ?? "";
  const key = [
    normalizeMemoryKey(subject),
    normalizeMemoryKey(predicate),
    normalizeMemoryKey(object),
    chunk.source_record_id,
    chunk.chunk_id
  ].join(":");

  if (seen.has(key)) {
    return;
  }
  seen.add(key);

  const status =
    candidate.confidence >= ACCEPTED_CONFIDENCE_THRESHOLD ? "accepted" : "candidate";

  db.prepare(
    [
      "insert into semantic_facts",
      "(subject, predicate, object, source_record_id, source_chunk_id, confidence, status)",
      "values (?, ?, ?, ?, ?, ?, ?)"
    ].join(" ")
  ).run(
    subject.trim(),
    predicate.trim(),
    object.trim(),
    chunk.source_record_id,
    chunk.chunk_id,
    candidate.confidence,
    status
  );
}

function snapshotAcceptedFacts(db: MemoryDatabase): ExistingFactSnapshot[] {
  return db
    .prepare(
      [
        "select subject, predicate, object, source_record_id, source_chunk_id, confidence, status",
        "from semantic_facts",
        "where status = 'accepted'"
      ].join(" ")
    )
    .all() as ExistingFactSnapshot[];
}

function restoreStaleFacts(
  db: MemoryDatabase,
  previousFacts: ExistingFactSnapshot[],
  seenFacts: Set<string>
): void {
  const insertStale = db.prepare(
    [
      "insert into semantic_facts",
      "(subject, predicate, object, source_record_id, source_chunk_id, confidence, status)",
      "values (?, ?, ?, ?, ?, ?, 'stale')"
    ].join(" ")
  );

  for (const fact of previousFacts) {
    const key = [
      normalizeMemoryKey(fact.subject),
      normalizeMemoryKey(fact.predicate),
      normalizeMemoryKey(fact.object),
      fact.source_record_id,
      fact.source_chunk_id
    ].join(":");
    if (seenFacts.has(key)) {
      continue;
    }
    insertStale.run(
      fact.subject,
      fact.predicate,
      fact.object,
      fact.source_record_id,
      fact.source_chunk_id,
      fact.confidence
    );
  }
}

function aliasKeyFor(type: EntityType, value: string): string {
  return `${type}:${normalizeMemoryKey(value)}`;
}

function inferEntityTypeForObject(relationshipType: RelationshipType): EntityType {
  if (relationshipType === "works_at") {
    return "organization";
  }
  if (relationshipType === "relevant_to_domain" || relationshipType === "needs_follow_up") {
    return "domain";
  }
  return "organization";
}

function inferEntityTypeForSubject(db: MemoryDatabase, subject: string): EntityType {
  const aliasKeySuffix = normalizeMemoryKey(subject);
  const existing = db
    .prepare(
      [
        "select entities.type as type",
        "from entity_aliases",
        "join entities on entities.id = entity_aliases.entity_id",
        "where entity_aliases.alias_key like ?",
        "order by entities.id",
        "limit 1"
      ].join(" ")
    )
    .get(`%:${aliasKeySuffix}`) as { type: EntityType } | undefined;

  return existing?.type ?? "person";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
