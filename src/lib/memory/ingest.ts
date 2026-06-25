import { readFile, readdir } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import type postgres from "postgres";
import { isSupportedSourcePath, parseSourceFile } from "../../frontmatter.js";
import { IngestError, classifyIngestError, type IngestErrorCategory } from "../../ingest-error.js";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor.js";
import { chunkCsvRows, chunkText, citationForChunk, sha256, slugifySourceId, type TextChunk } from "./chunk.js";
import { embedText, toVectorLiteral } from "./embed.js";

export type MemorySkipCategory = IngestErrorCategory | "unchanged";

export interface MemorySkippedFile {
  path: string;
  category: MemorySkipCategory;
  reason: string;
}

export interface MemoryIngestResult {
  processed: number;
  skipped: number;
  skippedFiles: MemorySkippedFile[];
}

type Sql = postgres.Sql;

// Internal signal for dedup skip — not an IngestError.
class UnchangedSkip {
  readonly message = "File content unchanged";
}

export async function ingestFolder(
  db: Sql,
  folderPath: string,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<MemoryIngestResult> {
  const folder = resolve(folderPath);
  const result: MemoryIngestResult = { processed: 0, skipped: 0, skippedFiles: [] };

  for (const filePath of await walkFiles(folder)) {
    if (!isSupportedSourcePath(filePath)) {
      recordSkip(result, filePath, new IngestError("unsupported-type", `Unsupported file extension: ${filePath}`));
      continue;
    }

    try {
      await ingestFile(db, filePath, folder, actor);
      result.processed += 1;
    } catch (error) {
      recordSkip(result, filePath, error);
    }
  }

  return result;
}

async function ingestFile(
  db: Sql,
  filePath: string,
  rootFolder: string,
  actor: MemoryActor
): Promise<void> {
  // Read
  let content: string;
  try {
    content = await readFile(filePath, "utf8");
  } catch (err) {
    throw new IngestError("unreadable", `Failed to read file: ${errorMessage(err)}`);
  }

  // Parse (throws IngestError on empty / parse failures)
  const parsed = parseSourceFile(filePath, content);

  // Chunk (throws IngestError on parse failures)
  const chunks = chunkSourceText(parsed.sourceType, parsed.rawText);

  const contentHash = sha256(parsed.rawText);
  const relativeLabel = sourceLabel(rootFolder, filePath);
  const sourceId = slugifySourceId(parsed.sourceId ?? relativeLabel);

  // Dedup: skip if the stored row already has the same content hash
  const [existing] = await db<{ content_hash: string }[]>`
    SELECT content_hash FROM source_records WHERE path = ${filePath}
  `;
  if (existing && existing.content_hash === contentHash) {
    throw new UnchangedSkip();
  }

  // Compute embeddings outside the transaction so model_calls tracking
  // (when OPENAI_API_KEY is set) is not rolled back on a chunk insert failure.
  const embeddings = await Promise.all(chunks.map((chunk) => embedText(db, chunk.text)));

  // Atomically: upsert source_record + replace chunks + seed permission
  await db.begin(async (tx) => {
    const [source] = await tx<{ id: string; source_id: string }[]>`
      INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
      VALUES (${sourceId}, ${filePath}, ${parsed.title}, ${parsed.sourceType}, ${contentHash}, ${parsed.rawText})
      ON CONFLICT (path) DO UPDATE SET
        title        = EXCLUDED.title,
        source_type  = EXCLUDED.source_type,
        content_hash = EXCLUDED.content_hash,
        raw_text     = EXCLUDED.raw_text,
        updated_at   = now()
      RETURNING id, source_id
    `;

    const effectiveSourceId = source.source_id;
    const sourceRecordId = source.id;

    await tx`DELETE FROM memory_chunks WHERE source_record_id = ${sourceRecordId}`;

    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i];
      const vectorLiteral = toVectorLiteral(embeddings[i]);
      const citation = citationForChunk(effectiveSourceId, parsed.sourceType, chunk);
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
}

async function walkFiles(folder: string): Promise<string[]> {
  const entries = (await readdir(folder, { withFileTypes: true })).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
  const files: string[] = [];

  for (const entry of entries) {
    if (entry.name === ".sourcyavo") {
      continue;
    }

    const entryPath = join(folder, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await walkFiles(entryPath)));
    } else {
      files.push(entryPath);
    }
  }

  return files;
}

function chunkSourceText(sourceType: string, rawText: string): TextChunk[] {
  return sourceType === "csv" ? chunkCsvRows(rawText) : chunkText(rawText);
}

function sourceLabel(rootFolder: string, filePath: string): string {
  const label = relative(rootFolder, filePath) || filePath;
  return label.split(sep).join("/");
}

function recordSkip(result: MemoryIngestResult, filePath: string, error: unknown): void {
  if (error instanceof UnchangedSkip) {
    result.skipped += 1;
    result.skippedFiles.push({ path: filePath, category: "unchanged", reason: error.message });
    return;
  }
  const category = classifyIngestError(error);
  const reason = errorMessage(error);
  result.skipped += 1;
  result.skippedFiles.push({ path: filePath, category, reason });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
