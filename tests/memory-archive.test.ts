import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { resolveAllowedSourceIds } from "@/lib/memory/permissions";
import { ingestFiles } from "@/lib/memory/ingest";
import { searchMemory } from "@/lib/memory/retrieve";

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

describe("memory archive (soft-archive + central filter)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) process.env.OPENAI_API_KEY = savedApiKey;
    else delete process.env.OPENAI_API_KEY;
    await closeDb();
  });

  it("003 migration adds an archived_at column to source_records", async () => {
    const db = getDb();
    const [col] = await db<{ data_type: string }[]>`
      SELECT data_type FROM information_schema.columns
      WHERE table_name = 'source_records' AND column_name = 'archived_at'
    `;
    expect(col?.data_type).toMatch(/timestamp/i);
  });

  it("resolveAllowedSourceIds excludes archived sources by default; includeArchived returns them", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") }]);
    const [{ source_id: sid }] = await db<{ source_id: string }[]>`SELECT source_id FROM source_records`;

    expect(await resolveAllowedSourceIds(db, DEFAULT_ACTOR)).toContain(sid);

    await db`UPDATE source_records SET archived_at = now() WHERE source_id = ${sid}`;

    expect(await resolveAllowedSourceIds(db, DEFAULT_ACTOR)).not.toContain(sid);
    expect(await resolveAllowedSourceIds(db, DEFAULT_ACTOR, { includeArchived: true })).toContain(sid);
  });

  it("searchMemory returns no chunks/facts for an archived source; un-archiving restores them", async () => {
    const db = getDb();
    await ingestFiles(db, [
      { name: "acme.md", bytes: bytes("# Acme Robotics\n\nAcme Robotics is a Series B sourcing target in Boston.") },
    ]);

    const before = await searchMemory(db, { query: "Acme Robotics sourcing target" });
    expect(before.chunks.length).toBeGreaterThan(0);

    await db`UPDATE source_records SET archived_at = now()`;
    const archived = await searchMemory(db, { query: "Acme Robotics sourcing target" });
    expect(archived.chunks).toHaveLength(0);
    expect(archived.acceptedFacts).toHaveLength(0);

    await db`UPDATE source_records SET archived_at = NULL`;
    const restored = await searchMemory(db, { query: "Acme Robotics sourcing target" });
    expect(restored.chunks.length).toBeGreaterThan(0);
  });
});
