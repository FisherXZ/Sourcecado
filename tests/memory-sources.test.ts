import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { ingestFiles } from "@/lib/memory/ingest";
import { searchMemory } from "@/lib/memory/retrieve";
import { listSources, setSourceArchived } from "@/lib/memory/sources";

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

describe("memory sources management (list + archive)", () => {
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

  it("listSources returns the actor's permitted sources including archived, with an archived flag", async () => {
    const db = getDb();
    await ingestFiles(db, [
      { name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") },
      { name: "beta.md", bytes: bytes("# Beta\n\nBeta is a sourcing target.") },
    ]);

    const list = await listSources(db, DEFAULT_ACTOR);
    expect(list).toHaveLength(2);
    expect(list.every((s) => s.archived === false)).toBe(true);
    expect(list.every((s) => s.sourceType === "markdown")).toBe(true);
    expect(list.every((s) => typeof s.sourceId === "string" && s.sourceId.length > 0)).toBe(true);

    const target = list[0].sourceId;
    await setSourceArchived(db, { sourceId: target, archived: true });

    const list2 = await listSources(db, DEFAULT_ACTOR);
    expect(list2).toHaveLength(2); // management view still shows archived
    expect(list2.find((s) => s.sourceId === target)?.archived).toBe(true);
  });

  it("setSourceArchived hides the source from searchMemory; un-archive restores it", async () => {
    const db = getDb();
    await ingestFiles(db, [
      { name: "acme.md", bytes: bytes("# Acme Robotics\n\nAcme Robotics is a sourcing target in Boston.") },
    ]);
    const [{ source_id: sid }] = await db<{ source_id: string }[]>`SELECT source_id FROM source_records`;

    const archived = await setSourceArchived(db, { sourceId: sid, archived: true });
    expect(archived).toEqual({ sourceId: sid, archived: true });
    expect((await searchMemory(db, { query: "Acme Robotics sourcing target" })).chunks).toHaveLength(0);

    const restored = await setSourceArchived(db, { sourceId: sid, archived: false });
    expect(restored).toEqual({ sourceId: sid, archived: false });
    expect((await searchMemory(db, { query: "Acme Robotics sourcing target" })).chunks.length).toBeGreaterThan(0);
  });

  it("setSourceArchived returns null for a source the actor cannot access", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme.") }]);
    const res = await setSourceArchived(db, { sourceId: "no-such-source", archived: true });
    expect(res).toBeNull();
  });
});
