import type postgres from "postgres";
import { slugifySourceId } from "../../db.js";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor.js";
import { chunkText, citationForChunk, sha256 } from "./chunk.js";
import { embedText, toVectorLiteral } from "./embed.js";

type Sql = postgres.Sql;

export async function addMemoryNote(
  db: Sql,
  args: { title: string; text: string; actor?: MemoryActor }
): Promise<{ sourceId: string }> {
  const { title, text, actor = DEFAULT_ACTOR } = args;

  const titleSlug = slugifySourceId(title);
  const sourceId = `note-${titleSlug}-${sha256(text).slice(0, 8)}`;
  const path = `note://${sourceId}`;
  const contentHash = sha256(text);

  const chunks = chunkText(text);

  // Embed outside the transaction so model_calls tracking is not rolled back
  // on a chunk insert failure (mirrors the pattern in ingest.ts).
  const embeddings = await Promise.all(chunks.map((chunk) => embedText(db, chunk.text)));

  await db.begin(async (tx) => {
    const [source] = await tx<{ id: string; source_id: string }[]>`
      INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
      VALUES (${sourceId}, ${path}, ${title}, 'note', ${contentHash}, ${text})
      ON CONFLICT (path) DO UPDATE SET
        title        = EXCLUDED.title,
        content_hash = EXCLUDED.content_hash,
        raw_text     = EXCLUDED.raw_text,
        updated_at   = now()
      RETURNING id, source_id
    `;

    const sourceRecordId = source.id;
    const effectiveSourceId = source.source_id;

    // Replace chunks to stay idempotent on re-add (same dedup pattern as ingest.ts).
    await tx`DELETE FROM memory_chunks WHERE source_record_id = ${sourceRecordId}`;

    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i];
      const vectorLiteral = toVectorLiteral(embeddings[i]);
      const citation = citationForChunk(effectiveSourceId, "note", chunk);
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
      VALUES (${actor.actorType}, ${actor.actorId}, ${effectiveSourceId}, 'read')
      ON CONFLICT (principal_type, principal_id, source_id) DO NOTHING
    `;
  });

  return { sourceId };
}
