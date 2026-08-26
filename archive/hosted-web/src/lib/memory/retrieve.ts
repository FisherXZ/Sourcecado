import type { Sql } from "../tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor";
import { embedText, toVectorLiteral } from "./embed";
import { resolveAllowedSourceIds } from "./permissions";
import {
  asksForUncertainty,
  lexicalScore,
  questionIntent,
  rankRows,
  type QuestionIntent,
} from "./rank";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface MemoryFact {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
  status: string;
  citation: string | null;
}

export interface MemoryChunk {
  text: string;
  citation: string;
  score: number;
}

export interface MemoryBundle {
  intent: QuestionIntent;
  acceptedFacts: MemoryFact[];
  gapFacts: MemoryFact[];
  chunks: MemoryChunk[];
}

// ---------------------------------------------------------------------------
// searchMemory — permission-filtered hybrid retrieval
// ---------------------------------------------------------------------------

export async function searchMemory(
  db: Sql,
  args: { query: string; actor?: MemoryActor; limit?: number }
): Promise<MemoryBundle> {
  const { query, actor = DEFAULT_ACTOR, limit = 6 } = args;

  const intent = questionIntent(query);
  const allowed = await resolveAllowedSourceIds(db, actor);

  // Default-deny: empty allowlist → no data returned immediately.
  if (allowed.length === 0) {
    return { intent, acceptedFacts: [], gapFacts: [], chunks: [] };
  }

  const [acceptedFacts, gapFacts, chunks] = await Promise.all([
    loadAcceptedFacts(db, query, intent, allowed, limit),
    loadGapFacts(db, query, allowed),
    loadChunks(db, query, allowed),
  ]);

  return { intent, acceptedFacts, gapFacts, chunks };
}

// ---------------------------------------------------------------------------
// Internal query helpers — permission filter is always in SQL WHERE
// ---------------------------------------------------------------------------

async function loadAcceptedFacts(
  db: Sql,
  query: string,
  intent: QuestionIntent,
  allowed: string[],
  limit: number
): Promise<MemoryFact[]> {
  // sr.source_id = ANY(${allowed}) is the permission gate — enforced in SQL,
  // never in JS, so a restricted source is never a ranking candidate.
  const rows = await db<MemoryFact[]>`
    SELECT
      sf.subject,
      sf.predicate,
      sf.object,
      sf.confidence,
      sf.status,
      mc.citation
    FROM semantic_facts sf
    JOIN source_records sr ON sr.id = sf.source_record_id
    LEFT JOIN memory_chunks mc ON mc.id = sf.source_chunk_id
    WHERE sf.status = 'accepted'
      AND sr.source_id = ANY(${allowed})
    ORDER BY sf.confidence DESC, sf.subject
  `;

  const ranked = rankRows(
    rows,
    query,
    intent,
    (r) => `${r.subject} ${r.predicate} ${r.object}`
  );
  return ranked.slice(0, limit);
}

async function loadGapFacts(db: Sql, query: string, allowed: string[]): Promise<MemoryFact[]> {
  const rows = await db<MemoryFact[]>`
    SELECT
      sf.subject,
      sf.predicate,
      sf.object,
      sf.confidence,
      sf.status,
      mc.citation
    FROM semantic_facts sf
    JOIN source_records sr ON sr.id = sf.source_record_id
    LEFT JOIN memory_chunks mc ON mc.id = sf.source_chunk_id
    WHERE sf.status IN ('candidate', 'conflicted', 'stale')
      AND sr.source_id = ANY(${allowed})
    ORDER BY sf.status, sf.subject
  `;

  if (asksForUncertainty(query)) {
    return rows.slice(0, 6);
  }

  return rankRows(
    rows,
    query,
    "generic",
    (r) => `${r.subject} ${r.predicate} ${r.object}`
  ).slice(0, 6);
}

interface RawChunkRow {
  text: string;
  citation: string;
  score: number;
}

async function loadChunks(db: Sql, query: string, allowed: string[]): Promise<MemoryChunk[]> {
  const qvec = await embedText(db, query);
  const qvecLiteral = toVectorLiteral(qvec);

  // Fetch top-10 candidates by cosine distance; permission filter is in SQL WHERE.
  // The ${qvecLiteral}::vector cast sends the vector literal as a bound parameter,
  // letting pgvector handle cosine arithmetic entirely in the database.
  const rows = await db<RawChunkRow[]>`
    SELECT
      mc.text,
      mc.citation,
      (1 - (mc.embedding <=> ${qvecLiteral}::vector))::float8 AS score
    FROM memory_chunks mc
    JOIN source_records sr ON sr.id = mc.source_record_id
    WHERE sr.source_id = ANY(${allowed})
      AND mc.embedding IS NOT NULL
    ORDER BY mc.embedding <=> ${qvecLiteral}::vector ASC
    LIMIT 10
  `;

  // JS post-filter: require both semantic overlap (score > 0) and at least one
  // meaningful query term in the text (lexical gate).
  return rows
    .filter((r) => r.score > 0 && lexicalScore(r.text, query) > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
}
