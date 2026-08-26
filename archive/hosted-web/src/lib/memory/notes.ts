import type postgres from "postgres";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor";
import { chunkText, sha256, slugifySourceId } from "./chunk";
import { writeChunksAndGrant } from "./chunk-store";
import { embedText } from "./embed";

type Sql = postgres.Sql;

export async function addMemoryNote(
  db: Sql,
  args: { title: string; text: string; actor?: MemoryActor; runId?: number }
): Promise<{ sourceId: string }> {
  const { title, text, actor = DEFAULT_ACTOR, runId } = args;

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
      INSERT INTO source_records (
        source_id, path, title, source_type, content_hash, raw_text,
        created_by_run_id, created_by_actor_type, created_by_actor_id
      )
      VALUES (
        ${sourceId}, ${path}, ${title}, 'note', ${contentHash}, ${text},
        ${runId ?? null}, ${actor.actorType}, ${actor.actorId}
      )
      ON CONFLICT (path) DO UPDATE SET
        title                 = EXCLUDED.title,
        content_hash          = EXCLUDED.content_hash,
        raw_text              = EXCLUDED.raw_text,
        -- Preserve an existing run attribution when a run-less path (e.g. the
        -- direct /api/memory/note route) re-adds identical text; a real writing
        -- run always wins over null so the note stays traceable.
        created_by_run_id     = COALESCE(EXCLUDED.created_by_run_id, source_records.created_by_run_id),
        created_by_actor_type = EXCLUDED.created_by_actor_type,
        created_by_actor_id   = EXCLUDED.created_by_actor_id,
        updated_at            = now()
      RETURNING id, source_id
    `;

    const sourceRecordId = source.id;
    const effectiveSourceId = source.source_id;

    // Replace chunks to stay idempotent on re-add (same dedup pattern as ingest.ts).
    await writeChunksAndGrant(tx, {
      sourceRecordId,
      sourceId: effectiveSourceId,
      sourceType: "note",
      chunks,
      embeddings,
      actor,
    });
  });

  return { sourceId };
}
