import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { ingestFiles } from "@/lib/memory/ingest";
import { searchMemory } from "@/lib/memory/retrieve";
import { listMemoryIndexRows, listSources, setSourceArchived } from "@/lib/memory/sources";

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

describe("listMemoryIndexRows", () => {
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

  async function insertSourceRow(
    db: ReturnType<typeof getDb>,
    opts: { sourceId: string; title: string; sourceType: string; updatedAt?: string }
  ): Promise<void> {
    await db`
      INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text, updated_at)
      VALUES (
        ${opts.sourceId}, ${"/test/" + opts.sourceId}, ${opts.title}, ${opts.sourceType},
        ${"hash-" + opts.sourceId}, '', ${opts.updatedAt ? new Date(opts.updatedAt) : new Date()}
      )
    `;
  }

  async function grantRead(db: ReturnType<typeof getDb>, sourceId: string): Promise<void> {
    await db`
      INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
      VALUES (${DEFAULT_ACTOR.actorType}, ${DEFAULT_ACTOR.actorId}, ${sourceId}, 'read')
    `;
  }

  it("returns only permitted, non-archived sources with title/type/date", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "a", title: "Alpha", sourceType: "markdown" });
    await insertSourceRow(db, { sourceId: "b", title: "Beta", sourceType: "csv" });
    await grantRead(db, "a");
    // "b" is never granted -> must not appear.

    const { sources, recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({ sourceId: "a", title: "Alpha", sourceType: "markdown" });
    expect(recentNotes).toHaveLength(0);
  });

  it("excludes archived sources", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "arch", title: "Archived", sourceType: "markdown" });
    await grantRead(db, "arch");
    await db`UPDATE source_records SET archived_at = now() WHERE source_id = 'arch'`;

    const { sources } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toHaveLength(0);
  });

  it("caps recentNotes to the 20 most-recently-updated note rows", async () => {
    const db = getDb();
    for (let i = 0; i < 25; i++) {
      const sourceId = `note-${i}`;
      await insertSourceRow(db, {
        sourceId,
        title: `Note ${i}`,
        sourceType: "note",
        updatedAt: new Date(Date.now() - (25 - i) * 1000).toISOString(),
      });
      await grantRead(db, sourceId);
    }

    const { recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(recentNotes).toHaveLength(20);
    // Most recently updated (note-24, inserted with the latest timestamp) is first.
    expect(recentNotes[0].sourceId).toBe("note-24");
  });

  it("splits notes out of sources — the two lists are disjoint", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "doc", title: "Doc", sourceType: "markdown" });
    await insertSourceRow(db, { sourceId: "note", title: "Note", sourceType: "note" });
    await grantRead(db, "doc");
    await grantRead(db, "note");

    const { sources, recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources.map((s) => s.sourceId)).toEqual(["doc"]);
    expect(recentNotes.map((s) => s.sourceId)).toEqual(["note"]);
    expect(sources.some((s) => s.sourceType === "note")).toBe(false);
  });

  it("returns empty lists when the actor has no permitted sources", async () => {
    const db = getDb();
    const { sources, recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toEqual([]);
    expect(recentNotes).toEqual([]);
  });
});
