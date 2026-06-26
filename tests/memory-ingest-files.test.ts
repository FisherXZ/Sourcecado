import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { ingestFiles } from "@/lib/memory/ingest";

function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

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

describe("memory ingestFiles (in-memory uploads)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    // Offline: hash-fallback embeddings (mirror memory-ingest.test.ts)
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
    await closeDb();
  });

  it("ingests uploaded files: writes source_records with upload:// path + chunks, grants DEFAULT_ACTOR read", async () => {
    const db = getDb();

    const result = await ingestFiles(db, [
      { name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") },
      { name: "contacts.csv", bytes: bytes("name,status\nJane,contacted\n") },
    ]);

    expect(result.processed).toBe(2);
    expect(result.skipped).toBe(0);

    const sources = await db<{ path: string }[]>`
      SELECT path FROM source_records ORDER BY path
    `;
    expect(sources.map((s) => s.path)).toEqual(["upload://acme.md", "upload://contacts.csv"]);

    const [{ n: grants }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM source_permissions
      WHERE principal_type = ${DEFAULT_ACTOR.actorType}
        AND principal_id = ${DEFAULT_ACTOR.actorId}
        AND access = 'read'
    `;
    expect(grants).toBe(2);

    const chunks = await db<{ citation: string }[]>`SELECT citation FROM memory_chunks`;
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    expect(chunks.every((c) => c.citation.length > 0)).toBe(true);
  });

  it("re-uploading the same filename with unchanged content is skipped (dedup); no duplicate chunks", async () => {
    const db = getDb();
    const content = "# Acme\n\nStable content.";

    const first = await ingestFiles(db, [{ name: "acme.md", bytes: bytes(content) }]);
    expect(first.processed).toBe(1);

    const [{ n: before }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM memory_chunks`;

    const second = await ingestFiles(db, [{ name: "acme.md", bytes: bytes(content) }]);
    expect(second.processed).toBe(0);
    expect(second.skipped).toBe(1);
    expect(second.skippedFiles[0].path).toBe("acme.md");
    expect(second.skippedFiles[0].reason).toMatch(/unchanged/i);

    const [{ n: after }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM memory_chunks`;
    expect(after).toBe(before);
  });

  it("re-uploading the same filename with changed content updates in place (same source_id, new chunks)", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nOld funding 5M.") }]);
    const [{ source_id: firstId }] = await db<{ source_id: string }[]>`SELECT source_id FROM source_records`;

    const result = await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nNew funding 10M.") }]);
    expect(result.processed).toBe(1);

    const recs = await db<{ source_id: string }[]>`SELECT source_id FROM source_records`;
    expect(recs).toHaveLength(1); // updated in place, not duplicated
    expect(recs[0].source_id).toBe(firstId);

    const chunks = await db<{ text: string }[]>`SELECT text FROM memory_chunks`;
    expect(chunks.some((c) => c.text.includes("New funding"))).toBe(true);
    expect(chunks.some((c) => c.text.includes("Old funding"))).toBe(false);
  });

  it("collision (a): two files with the same name in one batch — second skipped, first NOT overwritten", async () => {
    const db = getDb();

    const result = await ingestFiles(db, [
      { name: "notes.md", bytes: bytes("# Notes\n\nFirst file content.") },
      { name: "notes.md", bytes: bytes("# Notes\n\nSecond file content.") },
    ]);

    expect(result.processed).toBe(1);
    expect(result.skipped).toBe(1);
    expect(result.skippedFiles[0].path).toBe("notes.md");
    expect(result.skippedFiles[0].category).toBe("duplicate");
    expect(result.skippedFiles[0].reason).toMatch(/duplicate filename/i);

    const recs = await db<{ raw_text: string }[]>`SELECT raw_text FROM source_records`;
    expect(recs).toHaveLength(1);
    expect(recs[0].raw_text).toContain("First file content"); // first wins, no silent overwrite
  });

  it("collision (b): different names that slugify to the same source_id — friendly skip, not a raw DB error", async () => {
    const db = getDb();

    const result = await ingestFiles(db, [
      { name: "Acme Corp.md", bytes: bytes("# Acme\n\nFirst.") },
      { name: "acme-corp.md", bytes: bytes("# Acme\n\nSecond.") },
    ]);

    expect(result.processed).toBe(1);
    expect(result.skipped).toBe(1);

    const skip = result.skippedFiles[0];
    expect(skip.category).toBe("duplicate");
    expect(skip.reason).toMatch(/already uses id/i);
    expect(skip.reason).not.toMatch(/duplicate key value|violates|23505/i); // not a leaked PG error
  });

  it("unsupported file type is skipped with a reason; writes no source_records row", async () => {
    const db = getDb();

    const result = await ingestFiles(db, [
      { name: "ok.txt", bytes: bytes("Good content.") },
      { name: "image.png", bytes: bytes("binary data") },
    ]);

    expect(result.processed).toBe(1);
    expect(result.skipped).toBe(1);
    expect(result.skippedFiles[0].path).toBe("image.png");
    expect(result.skippedFiles[0].reason.length).toBeGreaterThan(0);

    const recs = await db<{ path: string }[]>`SELECT path FROM source_records`;
    expect(recs.every((r) => !r.path.includes("image.png"))).toBe(true);
  });

  it("empty file is skipped with an 'empty' reason and no orphan rows", async () => {
    const db = getDb();

    const result = await ingestFiles(db, [{ name: "empty.md", bytes: bytes("   \n\t") }]);

    expect(result.processed).toBe(0);
    expect(result.skipped).toBe(1);
    expect(result.skippedFiles[0].reason).toMatch(/empty/i);

    const [{ n }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM source_records`;
    expect(n).toBe(0);
  });
});
