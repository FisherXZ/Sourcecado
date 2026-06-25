import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { ingestFolder } from "@/lib/memory/ingest";

async function resetMemoryTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS source_permissions CASCADE`;
  await db`DROP TABLE IF EXISTS extraction_runs CASCADE`;
  await db`DROP TABLE IF EXISTS semantic_facts CASCADE`;
  await db`DROP TABLE IF EXISTS memory_chunks CASCADE`;
  await db`DROP TABLE IF EXISTS source_records CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

const tempDirs: string[] = [];

function makeTempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "memory-ingest-test-"));
  tempDirs.push(dir);
  return dir;
}

describe("memory ingestFolder (postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    // Unset OPENAI_API_KEY so embedText uses the deterministic hash fallback
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) {
      process.env.OPENAI_API_KEY = savedApiKey;
    } else {
      delete process.env.OPENAI_API_KEY;
    }
    for (const dir of tempDirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
    await closeDb();
  });

  it("writes source_records + memory_chunks with 1536-dim embeddings and non-empty citations", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "note.md"), "---\ntitle: Test Note\n---\nContent about sourcing.");
    writeFileSync(join(dir, "contacts.csv"), "name,status\nJane,contacted\n");

    const result = await ingestFolder(db, dir);

    expect(result.processed).toBe(2);
    expect(result.skipped).toBe(0);
    expect(result.skippedFiles).toHaveLength(0);

    const sources = await db<{ id: string; source_type: string }[]>`
      SELECT id, source_type FROM source_records ORDER BY id
    `;
    expect(sources).toHaveLength(2);

    const chunks = await db<{ citation: string }[]>`
      SELECT citation FROM memory_chunks ORDER BY id
    `;
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    expect(chunks.every((c) => c.citation.length > 0)).toBe(true);

    const dims = await db<{ d: number }[]>`
      SELECT vector_dims(embedding) AS d FROM memory_chunks LIMIT 1
    `;
    expect(dims[0].d).toBe(1536);
  });

  it("md/txt chunk citation ends with #chunk-1 (1-indexed); csv row citation ends with #row-1", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "note.txt"), "Simple sourcing note.");
    writeFileSync(join(dir, "data.csv"), "name,status\nJane,contacted\n");

    await ingestFolder(db, dir);

    const chunks = await db<{ citation: string }[]>`
      SELECT mc.citation
      FROM memory_chunks mc
      JOIN source_records sr ON sr.id = mc.source_record_id
      WHERE mc.chunk_index = 0
      ORDER BY mc.id
    `;

    const txtCitation = chunks.find((c) => c.citation.includes("#chunk-"));
    const csvCitation = chunks.find((c) => c.citation.includes("#row-"));

    expect(txtCitation?.citation).toMatch(/#chunk-1$/);
    expect(csvCitation?.citation).toMatch(/#row-1$/);
  });

  it("dedup: second run on unchanged folder skips all files and does not duplicate chunks", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "note.txt"), "Content to be deduplicated.");

    const firstResult = await ingestFolder(db, dir);
    expect(firstResult.processed).toBe(1);
    expect(firstResult.skipped).toBe(0);

    const [{ n: countBefore }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM memory_chunks
    `;

    const secondResult = await ingestFolder(db, dir);
    expect(secondResult.skipped).toBe(1);
    expect(secondResult.processed).toBe(0);
    expect(secondResult.skippedFiles[0].reason).toMatch(/unchanged/i);

    const [{ n: countAfter }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM memory_chunks
    `;
    expect(countAfter).toBe(countBefore);
  });

  it("content change replaces chunks: old text gone, new present, no orphan rows", async () => {
    const db = getDb();
    const dir = makeTempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "Original content for sourcing.");

    await ingestFolder(db, dir);

    const [oldChunk] = await db<{ text: string }[]>`SELECT text FROM memory_chunks LIMIT 1`;
    expect(oldChunk.text).toContain("Original content");

    writeFileSync(file, "New content after an update.");
    await ingestFolder(db, dir);

    const chunks = await db<{ text: string }[]>`SELECT text FROM memory_chunks`;
    expect(chunks.some((c) => c.text.includes("Original content"))).toBe(false);
    expect(chunks.some((c) => c.text.includes("New content"))).toBe(true);

    const [{ n: orphans }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n
      FROM memory_chunks mc
      LEFT JOIN source_records sr ON sr.id = mc.source_record_id
      WHERE sr.id IS NULL
    `;
    expect(orphans).toBe(0);
  });

  it("grants DEFAULT_ACTOR read permission on each ingested source_id", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "note.txt"), "Sourcing note.");
    writeFileSync(join(dir, "data.csv"), "name,status\nJane,contacted\n");

    await ingestFolder(db, dir);

    const perms = await db<{
      principal_type: string;
      principal_id: string;
      access: string;
    }[]>`
      SELECT principal_type, principal_id, access FROM source_permissions ORDER BY source_id
    `;
    expect(perms).toHaveLength(2);
    expect(perms.every((p) => p.principal_type === DEFAULT_ACTOR.actorType)).toBe(true);
    expect(perms.every((p) => p.principal_id === DEFAULT_ACTOR.actorId)).toBe(true);
    expect(perms.every((p) => p.access === "read")).toBe(true);
  });

  it("re-ingesting after permission is manually seeded preserves the existing permission row", async () => {
    const db = getDb();
    const dir = makeTempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "Sourcing note.");

    await ingestFolder(db, dir);

    const [{ source_id }] = await db<{ source_id: string }[]>`
      SELECT source_id FROM source_records LIMIT 1
    `;
    await db`
      INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
      VALUES ('user', 'alice', ${source_id}, 'read')
      ON CONFLICT DO NOTHING
    `;

    writeFileSync(file, "Updated sourcing note.");
    await ingestFolder(db, dir);

    const perms = await db<{ principal_id: string }[]>`
      SELECT principal_id FROM source_permissions WHERE principal_id = 'alice'
    `;
    expect(perms).toHaveLength(1);
  });

  it("unsupported file is in skippedFiles with a reason; no orphan source_records row", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "ok.txt"), "Good content.");
    writeFileSync(join(dir, "image.png"), "binary data");

    const result = await ingestFolder(db, dir);

    expect(result.processed).toBe(1);
    expect(result.skipped).toBe(1);
    expect(result.skippedFiles).toHaveLength(1);
    expect(result.skippedFiles[0].path).toContain("image.png");
    expect(result.skippedFiles[0].reason.length).toBeGreaterThan(0);

    const sources = await db<{ path: string }[]>`SELECT path FROM source_records`;
    expect(sources.every((s) => !s.path.includes("image.png"))).toBe(true);
  });

  it("frontmatter source_id with spaces/punctuation is slugified before storage so citations match the citation regex", async () => {
    const db = getDb();
    const dir = makeTempDir();
    // source_id with spaces and punctuation — would break the citation regex if stored as-is
    writeFileSync(
      join(dir, "note.md"),
      "---\ntitle: My Source\nsource_id: My Src: 2026\n---\nContent about sourcing candidates."
    );

    const result = await ingestFolder(db, dir);
    expect(result.processed).toBe(1);

    const [record] = await db<{ source_id: string }[]>`
      SELECT source_id FROM source_records LIMIT 1
    `;
    // source_id must conform to the citation charset
    expect(record.source_id).toMatch(/^[A-Za-z0-9._/-]+$/);

    const chunks = await db<{ citation: string }[]>`
      SELECT citation FROM memory_chunks ORDER BY chunk_index
    `;
    expect(chunks.length).toBeGreaterThan(0);
    // Every stored citation must match the citation token pattern
    const citationPattern = /^[A-Za-z0-9._-]+#(?:chunk|row)-\d+$/;
    for (const chunk of chunks) {
      expect(chunk.citation).toMatch(citationPattern);
    }
  });

  it("empty file is reported in skippedFiles with no orphan rows", async () => {
    const db = getDb();
    const dir = makeTempDir();
    writeFileSync(join(dir, "empty.md"), "   \n\t");

    const result = await ingestFolder(db, dir);

    expect(result.processed).toBe(0);
    expect(result.skipped).toBe(1);
    expect(result.skippedFiles[0].reason).toMatch(/empty/i);

    const [{ n }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM source_records
    `;
    expect(n).toBe(0);
  });
});
