import type postgres from "postgres";
import type { MemoryActor } from "./actor";
import { citationForChunk, type TextChunk } from "./chunk";
import { toVectorLiteral } from "./embed";

// Accepts either a root handle or a transaction handle (callers pass the `tx`
// from a `db.begin` callback; webpack type-checks this, vitest does not).
type Sql = postgres.Sql | postgres.TransactionSql;

/**
 * Shared transaction helper: DELETE old chunks for a source record, INSERT the
 * new chunks with embeddings, and seed the read permission grant.
 *
 * `tx` must be a postgres transaction handle (inside a `db.begin` callback).
 * `chunks` and `embeddings` must be positionally aligned.
 */
export async function writeChunksAndGrant(
  tx: Sql,
  args: {
    sourceRecordId: string;
    sourceId: string;
    sourceType: string;
    chunks: TextChunk[];
    embeddings: number[][];
    actor: MemoryActor;
  }
): Promise<void> {
  const { sourceRecordId, sourceId, sourceType, chunks, embeddings, actor } = args;

  await tx`DELETE FROM memory_chunks WHERE source_record_id = ${sourceRecordId}`;

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    const vectorLiteral = toVectorLiteral(embeddings[i]);
    const citation = citationForChunk(sourceId, sourceType, chunk);
    await tx`
      INSERT INTO memory_chunks (source_record_id, chunk_index, text, chunk_hash, embedding, citation)
      VALUES (
        ${sourceRecordId},
        ${chunk.chunkIndex},
        ${chunk.text},
        ${chunk.chunkHash},
        ${vectorLiteral}::vector,
        ${citation}
      )
    `;
  }

  await tx`
    INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
    VALUES (${actor.actorType}, ${actor.actorId}, ${sourceId}, 'read')
    ON CONFLICT (principal_type, principal_id, source_id) DO NOTHING
  `;
}
