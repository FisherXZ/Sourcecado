import { readFileSync, readdirSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { slugifySourceId, type MemoryDatabase } from "./db.js";
import { chunkCsvRows, chunkText, sha256, type TextChunk } from "./chunk.js";
import { serializeEmbedding } from "./embeddings.js";
import { isSupportedSourcePath, parseSourceFile } from "./frontmatter.js";
import {
  IngestError,
  classifyIngestError,
  type IngestErrorCategory
} from "./ingest-error.js";

export interface SkippedFile {
  path: string;
  category: IngestErrorCategory;
  reason: string;
}

export interface IngestResult {
  processed: number;
  skipped: number;
  skippedFiles: SkippedFile[];
}

interface SourceRecordInsertResult {
  id: number;
}

export function ingestFolder(db: MemoryDatabase, folderPath: string): IngestResult {
  const folder = resolve(folderPath);
  const result: IngestResult = { processed: 0, skipped: 0, skippedFiles: [] };

  for (const filePath of walkFiles(folder)) {
    if (!isSupportedSourcePath(filePath)) {
      recordSkip(
        db,
        result,
        filePath,
        new IngestError("unsupported-type", `Unsupported file extension: ${filePath}`)
      );
      continue;
    }

    try {
      ingestFile(db, filePath, folder);
      result.processed += 1;
    } catch (error) {
      recordSkip(db, result, filePath, error);
    }
  }

  return result;
}

function recordSkip(
  db: MemoryDatabase,
  result: IngestResult,
  filePath: string,
  error: unknown
): void {
  const category = classifyIngestError(error);
  const reason = errorMessage(error);
  logIngestError(db, filePath, category, reason);
  result.skipped += 1;
  result.skippedFiles.push({ path: filePath, category, reason });
}

function ingestFile(db: MemoryDatabase, filePath: string, rootFolder: string): void {
  const runInTransaction = db.transaction((path: string) => {
    const content = readSourceFile(path);
    const parsed = parseSourceFile(path, content);
    const chunks = chunkSourceText(parsed.sourceType, parsed.rawText);
    const contentHash = sha256(parsed.rawText);
    const relativeLabel = sourceLabel(rootFolder, path);
    const citationLabel = relativeLabel;
    const sourceId = parsed.sourceId ?? slugifySourceId(relativeLabel);

    const sourceRowId = upsertSourceRecord(db, {
      path,
      sourceId,
      title: parsed.title,
      sourceType: parsed.sourceType,
      contentHash,
      rawText: parsed.rawText
    });

    db.prepare("delete from memory_chunks where source_record_id = ?").run(sourceRowId);

    const insertChunk = db.prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, embedding, citation) values (?, ?, ?, ?, ?, ?)"
    );

    for (const chunk of chunks) {
      insertChunk.run(
        sourceRowId,
        chunk.chunkIndex,
        chunk.text,
        chunk.chunkHash,
        embedChunk(chunk),
        citationForChunk(citationLabel, parsed.sourceType, chunk)
      );
    }
  });

  runInTransaction(filePath);
}

function walkFiles(folder: string): string[] {
  const entries = readdirSync(folder, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
  const files: string[] = [];

  for (const entry of entries) {
    if (entry.name === ".sourcyavo") {
      continue;
    }

    const entryPath = join(folder, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkFiles(entryPath));
    } else {
      files.push(entryPath);
    }
  }

  return files;
}

function chunkSourceText(sourceType: string, rawText: string): TextChunk[] {
  return sourceType === "csv" ? chunkCsvRows(rawText) : chunkText(rawText);
}

function citationForChunk(source: string, sourceType: string, chunk: TextChunk): string {
  const anchor = sourceType === "csv" ? "row" : "chunk";
  return `${source}#${anchor}-${chunk.chunkIndex + 1}`;
}

function sourceLabel(rootFolder: string, filePath: string): string {
  const label = relative(rootFolder, filePath) || filePath;
  return label.split(sep).join("/");
}

function readSourceFile(filePath: string): string {
  try {
    return readFileSync(filePath, "utf8");
  } catch (error) {
    throw new IngestError("unreadable", `Failed to read file: ${errorMessage(error)}`);
  }
}

function embedChunk(chunk: TextChunk): string {
  try {
    return serializeEmbedding(chunk.text);
  } catch (error) {
    throw new IngestError("embedding-error", `Failed to embed chunk: ${errorMessage(error)}`);
  }
}

function upsertSourceRecord(
  db: MemoryDatabase,
  source: {
    path: string;
    sourceId: string;
    title: string;
    sourceType: string;
    contentHash: string;
    rawText: string;
  }
): number {
  // source_id is intentionally NOT updated on conflict: identity is preserved
  // across reimports even when frontmatter source_id changes.
  db.prepare(
    [
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text)",
      "values (?, ?, ?, ?, ?, ?)",
      "on conflict(path) do update set",
      "title = excluded.title,",
      "source_type = excluded.source_type,",
      "content_hash = excluded.content_hash,",
      "raw_text = excluded.raw_text,",
      "updated_at = datetime('now')"
    ].join(" ")
  ).run(
    source.path,
    source.sourceId,
    source.title,
    source.sourceType,
    source.contentHash,
    source.rawText
  );

  const row = db.prepare("select id from source_records where path = ?").get(source.path) as
    | SourceRecordInsertResult
    | undefined;
  if (!row) {
    throw new Error("Failed to load source record after upsert");
  }

  return row.id;
}

function logIngestError(
  db: MemoryDatabase,
  filePath: string,
  category: IngestErrorCategory,
  reason: string
): void {
  db.prepare("insert into ingest_errors (path, category, reason) values (?, ?, ?)").run(
    filePath,
    category,
    reason
  );
}

export function formatIngestReport(result: IngestResult, rootFolder: string): string {
  const summary = `Ingested ${result.processed} source file${
    result.processed === 1 ? "" : "s"
  }; skipped ${result.skipped}.`;

  if (result.skipped === 0) {
    return summary;
  }

  const root = resolve(rootFolder);
  const lines = [summary, "", "Skipped files:"];
  for (const skipped of result.skippedFiles) {
    // Use the relative label only; raw reasons may embed absolute paths.
    lines.push(`  - ${sourceLabel(root, skipped.path)} [${skipped.category}]`);
  }

  lines.push("", "Skipped by category:");
  for (const [category, count] of tallyByCategory(result.skippedFiles)) {
    lines.push(`  ${category}: ${count}`);
  }

  return lines.join("\n");
}

function tallyByCategory(skippedFiles: SkippedFile[]): Array<[IngestErrorCategory, number]> {
  const tally = new Map<IngestErrorCategory, number>();
  for (const skipped of skippedFiles) {
    tally.set(skipped.category, (tally.get(skipped.category) ?? 0) + 1);
  }

  return [...tally.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
