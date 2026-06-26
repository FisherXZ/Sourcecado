import { readFile, readdir } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import type postgres from "postgres";
import { isSupportedSourcePath, parseSourceFile } from "../../frontmatter.js";
import { IngestError, classifyIngestError, type IngestErrorCategory } from "../../ingest-error.js";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor";
import { chunkCsvRows, chunkText, sha256, slugifySourceId, type TextChunk } from "./chunk";
import { writeChunksAndGrant } from "./chunk-store";
import { embedText } from "./embed";

export type MemorySkipCategory = IngestErrorCategory | "unchanged" | "duplicate";

export interface MemorySkippedFile {
  // The file's user-facing identity: the relative path for folder ingest,
  // the original filename for uploads.
  path: string;
  category: MemorySkipCategory;
  reason: string;
}

export interface MemoryIngestResult {
  processed: number;
  skipped: number;
  skippedFiles: MemorySkippedFile[];
}

export interface UploadFile {
  name: string;
  bytes: Uint8Array;
}

type Sql = postgres.Sql;

// Internal signal for dedup skip — not an IngestError.
class UnchangedSkip {
  readonly message = "File content unchanged";
}

// Internal signal for a friendly duplicate/collision skip — not an IngestError.
// Surfaced instead of a raw Postgres UNIQUE error so the import UI stays readable.
class DuplicateSkip {
  constructor(readonly message: string) {}
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

// Ingest in-memory uploaded files (no disk). Each upload gets a stable
// filename identity: path = `upload://{name}`, name-derived source_id. So
// re-uploading the same filename dedups/updates in place, and citations stay
// clean. Two collisions become friendly per-file skips rather than DB errors:
//   (a) the same filename twice in one batch (would silently overwrite)
//   (b) different names that slugify to the same source_id (UNIQUE violation)
export async function ingestFiles(
  db: Sql,
  files: UploadFile[],
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<MemoryIngestResult> {
  const result: MemoryIngestResult = { processed: 0, skipped: 0, skippedFiles: [] };
  const seenPaths = new Set<string>();

  for (const file of files) {
    const storedPath = `upload://${file.name}`;

    // Collision (a): same filename twice in one batch. Skip the later one
    // rather than let ON CONFLICT (path) silently overwrite the first.
    if (seenPaths.has(storedPath)) {
      recordSkip(result, file.name, new DuplicateSkip("duplicate filename in this upload"));
      continue;
    }
    seenPaths.add(storedPath);

    if (!isSupportedSourcePath(file.name)) {
      recordSkip(result, file.name, new IngestError("unsupported-type", `Unsupported file extension: ${file.name}`));
      continue;
    }

    try {
      await ingestParsedSource(db, {
        parseName: file.name,
        storedPath,
        label: file.name,
        content: new TextDecoder().decode(file.bytes),
        actor,
      });
      result.processed += 1;
    } catch (error) {
      recordSkip(result, file.name, error);
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
  let content: string;
  try {
    content = await readFile(filePath, "utf8");
  } catch (err) {
    throw new IngestError("unreadable", `Failed to read file: ${errorMessage(err)}`);
  }

  await ingestParsedSource(db, {
    parseName: filePath,
    storedPath: filePath,
    label: sourceLabel(rootFolder, filePath),
    content,
    actor,
  });
}

// Shared core: parse → chunk → dedup → embed → atomically upsert the source
// record, replace its chunks, and seed the read grant. The three names let
// folder and upload callers map their own identity onto the same pipeline:
//   parseName  — for parseSourceFile (extension/type + title)
//   storedPath — source_records.path (dedup key + ON CONFLICT target)
//   label      — slugifySourceId fallback when there is no frontmatter source_id
async function ingestParsedSource(
  db: Sql,
  args: { parseName: string; storedPath: string; label: string; content: string; actor: MemoryActor }
): Promise<void> {
  const { parseName, storedPath, label, content, actor } = args;

  // Parse (throws IngestError on empty / parse failures)
  const parsed = parseSourceFile(parseName, content);

  // Chunk (throws IngestError on parse failures)
  const chunks = chunkSourceText(parsed.sourceType, parsed.rawText);

  const contentHash = sha256(parsed.rawText);
  const sourceId = slugifySourceId(parsed.sourceId ?? label);

  // Dedup: skip if the stored row already has the same content hash
  const [existing] = await db<{ content_hash: string }[]>`
    SELECT content_hash FROM source_records WHERE path = ${storedPath}
  `;
  if (existing && existing.content_hash === contentHash) {
    throw new UnchangedSkip();
  }

  // Collision: a different source (different path) already claims this
  // source_id. Surface a friendly skip instead of tripping the source_id
  // UNIQUE constraint, which would throw a raw Postgres error.
  const [collision] = await db<{ path: string }[]>`
    SELECT path FROM source_records WHERE source_id = ${sourceId} AND path != ${storedPath} LIMIT 1
  `;
  if (collision) {
    throw new DuplicateSkip(`a different source already uses id '${sourceId}'`);
  }

  // Compute embeddings outside the transaction so model_calls tracking
  // (when OPENAI_API_KEY is set) is not rolled back on a chunk insert failure.
  const embeddings = await Promise.all(chunks.map((chunk) => embedText(db, chunk.text)));

  // Atomically: upsert source_record + replace chunks + seed permission.
  try {
    await db.begin(async (tx) => {
      const [source] = await tx<{ id: string; source_id: string }[]>`
        INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
        VALUES (${sourceId}, ${storedPath}, ${parsed.title}, ${parsed.sourceType}, ${contentHash}, ${parsed.rawText})
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

      await writeChunksAndGrant(tx, {
        sourceRecordId,
        sourceId: effectiveSourceId,
        sourceType: parsed.sourceType,
        chunks,
        embeddings,
        actor,
      });
    });
  } catch (error) {
    // TOCTOU backstop: the collision pre-check above is non-atomic (embeddings
    // are computed between it and this INSERT), so two concurrent ingests of
    // different paths that slugify to the same source_id can both pass the check
    // and race here. Path conflicts are absorbed by ON CONFLICT (path), and the
    // chunk/permission writes can't trip a UNIQUE in this txn, so a unique
    // violation reaching here is necessarily source_id — surface the same
    // friendly skip the pre-check would, not a raw Postgres error.
    if (isSourceIdConflict(error)) {
      throw new DuplicateSkip(`a different source already uses id '${sourceId}'`);
    }
    throw error;
  }
}

// A Postgres unique_violation (SQLSTATE 23505) on the source_id constraint.
function isSourceIdConflict(error: unknown): boolean {
  const e = error as { code?: string; constraint_name?: string } | null;
  return e?.code === "23505" && (e.constraint_name == null || e.constraint_name.includes("source_id"));
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

function recordSkip(result: MemoryIngestResult, identifier: string, error: unknown): void {
  if (error instanceof UnchangedSkip) {
    result.skipped += 1;
    result.skippedFiles.push({ path: identifier, category: "unchanged", reason: error.message });
    return;
  }
  if (error instanceof DuplicateSkip) {
    result.skipped += 1;
    result.skippedFiles.push({ path: identifier, category: "duplicate", reason: error.message });
    return;
  }
  const category = classifyIngestError(error);
  const reason = errorMessage(error);
  result.skipped += 1;
  result.skippedFiles.push({ path: identifier, category, reason });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
