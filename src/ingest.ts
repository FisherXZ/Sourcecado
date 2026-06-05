import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { MemoryDatabase } from "./db.js";
import { chunkText, sha256 } from "./chunk.js";
import { serializeEmbedding } from "./embeddings.js";
import { isSupportedSourcePath, parseSourceFile } from "./frontmatter.js";

export interface IngestResult {
  processed: number;
  skipped: number;
}

interface SourceRecordInsertResult {
  id: number;
}

export function ingestFolder(db: MemoryDatabase, folderPath: string): IngestResult {
  const folder = resolve(folderPath);
  const entries = readdirSync(folder, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
  const result: IngestResult = { processed: 0, skipped: 0 };

  for (const entry of entries) {
    if (entry.isDirectory() || entry.name === ".sourcyavo") {
      continue;
    }

    const filePath = join(folder, entry.name);

    if (!isSupportedSourcePath(filePath)) {
      logIngestError(db, filePath, `Unsupported file extension: ${entry.name}`);
      result.skipped += 1;
      continue;
    }

    try {
      ingestFile(db, filePath);
      result.processed += 1;
    } catch (error) {
      logIngestError(db, filePath, formatIngestError(error));
      result.skipped += 1;
    }
  }

  return result;
}

function ingestFile(db: MemoryDatabase, filePath: string): void {
  const runInTransaction = db.transaction((path: string) => {
    const content = readSourceFile(path);
    const parsed = parseSourceFile(path, content);
    const chunks = chunkText(parsed.rawText);
    const contentHash = sha256(parsed.rawText);

    const sourceId = upsertSourceRecord(db, {
      path,
      title: parsed.title,
      sourceType: parsed.sourceType,
      contentHash,
      rawText: parsed.rawText
    });

    db.prepare("delete from memory_chunks where source_record_id = ?").run(sourceId);

    const insertChunk = db.prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, embedding, citation) values (?, ?, ?, ?, ?, ?)"
    );

    for (const chunk of chunks) {
      insertChunk.run(
        sourceId,
        chunk.chunkIndex,
        chunk.text,
        chunk.chunkHash,
        serializeEmbedding(chunk.text),
        `${path}#chunk-${chunk.chunkIndex + 1}`
      );
    }
  });

  runInTransaction(filePath);
}

function readSourceFile(filePath: string): string {
  try {
    return readFileSync(filePath, "utf8");
  } catch (error) {
    throw new Error(`Failed to read file: ${errorMessage(error)}`);
  }
}

function upsertSourceRecord(
  db: MemoryDatabase,
  source: {
    path: string;
    title: string;
    sourceType: string;
    contentHash: string;
    rawText: string;
  }
): number {
  db.prepare(
    [
      "insert into source_records (path, title, source_type, content_hash, raw_text)",
      "values (?, ?, ?, ?, ?)",
      "on conflict(path) do update set",
      "title = excluded.title,",
      "source_type = excluded.source_type,",
      "content_hash = excluded.content_hash,",
      "raw_text = excluded.raw_text,",
      "updated_at = datetime('now')"
    ].join(" ")
  ).run(source.path, source.title, source.sourceType, source.contentHash, source.rawText);

  const row = db.prepare("select id from source_records where path = ?").get(source.path) as
    | SourceRecordInsertResult
    | undefined;
  if (!row) {
    throw new Error("Failed to load source record after upsert");
  }

  return row.id;
}

function logIngestError(db: MemoryDatabase, filePath: string, reason: string): void {
  db.prepare("insert into ingest_errors (path, reason) values (?, ?)").run(filePath, reason);
}

function formatIngestError(error: unknown): string {
  return errorMessage(error);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
