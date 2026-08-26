import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { addMemoryNote } from "@/lib/memory/notes";
import { searchMemory } from "@/lib/memory/retrieve";
import { addMemoryNoteTool } from "@/lib/tools/add-memory-note";
import { startRun } from "@/lib/ledger";

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

describe("addMemoryNote (postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
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

  it("writes a source_records row with source_type='note' and returns { sourceId }", async () => {
    const db = getDb();
    const { sourceId } = await addMemoryNote(db, {
      title: "Sourcing Note",
      text: "Alice responded to outreach.",
    });

    expect(typeof sourceId).toBe("string");
    expect(sourceId.length).toBeGreaterThan(0);

    const [row] = await db<{ source_type: string; path: string; title: string }[]>`
      SELECT source_type, path, title FROM source_records WHERE source_id = ${sourceId}
    `;
    expect(row).toBeDefined();
    expect(row.source_type).toBe("note");
    expect(row.path).toBe(`note://${sourceId}`);
    expect(row.title).toBe("Sourcing Note");
  });

  it("writes memory_chunks with 1536-dim embedding and #chunk-1 citation", async () => {
    const db = getDb();
    const { sourceId } = await addMemoryNote(db, {
      title: "Test Note",
      text: "Alice responded to outreach.",
    });

    const chunks = await db<{ citation: string; embedding_dims: number }[]>`
      SELECT mc.citation, vector_dims(mc.embedding) AS embedding_dims
      FROM memory_chunks mc
      JOIN source_records sr ON sr.id = mc.source_record_id
      WHERE sr.source_id = ${sourceId}
      ORDER BY mc.chunk_index
    `;
    expect(chunks.length).toBeGreaterThan(0);
    expect(chunks[0].citation).toBe(`${sourceId}#chunk-1`);
    expect(chunks[0].embedding_dims).toBe(1536);
  });

  it("grants DEFAULT_ACTOR read permission on the source", async () => {
    const db = getDb();
    const { sourceId } = await addMemoryNote(db, {
      title: "Perm Note",
      text: "Some sourcing content.",
    });

    const [perm] = await db<{ principal_type: string; principal_id: string; access: string }[]>`
      SELECT principal_type, principal_id, access
      FROM source_permissions
      WHERE source_id = ${sourceId}
    `;
    expect(perm).toBeDefined();
    expect(perm.principal_type).toBe(DEFAULT_ACTOR.actorType);
    expect(perm.principal_id).toBe(DEFAULT_ACTOR.actorId);
    expect(perm.access).toBe("read");
  });

  it("is immediately retrievable via searchMemory", async () => {
    const db = getDb();
    const text = "Apollo sourcing pipeline candidates outreach contact";
    const { sourceId } = await addMemoryNote(db, { title: "Pipeline Note", text });

    const bundle = await searchMemory(db, {
      query: "Apollo sourcing pipeline candidates",
    });

    const found = bundle.chunks.some((c) => c.citation.startsWith(sourceId));
    expect(found).toBe(true);
  });

  it("correction: same title, new text → distinct sourceId; corrected content retrievable", async () => {
    const db = getDb();
    const title = "Sourcing Strategy";
    const { sourceId: id1 } = await addMemoryNote(db, {
      title,
      text: "Original outreach plan for pipeline candidates overview.",
    });
    const { sourceId: id2 } = await addMemoryNote(db, {
      title,
      text: "Updated outreach strategy candidates silicon valley recruiting.",
    });

    expect(id1).not.toBe(id2);

    const records = await db<{ source_id: string }[]>`
      SELECT source_id FROM source_records WHERE source_id = ANY(${[id1, id2]})
    `;
    expect(records).toHaveLength(2);

    const bundle = await searchMemory(db, {
      query: "Updated outreach strategy silicon valley recruiting",
    });
    const found = bundle.chunks.some((c) => c.citation.startsWith(id2));
    expect(found).toBe(true);
  });

  it("stamps the writing run and actor onto the source row (poisoned-note traceability)", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "agent_chat", title: "note-provenance", input: {} });

    const { sourceId } = await addMemoryNoteTool.execute(
      { title: "Injected Note", text: "Possibly prompt-injected content." },
      { db, runId: run.id, parentStepId: 0 }
    );

    const [row] = await db<
      { created_by_run_id: string | null; created_by_actor_type: string; created_by_actor_id: string }[]
    >`
      SELECT created_by_run_id, created_by_actor_type, created_by_actor_id
      FROM source_records WHERE source_id = ${sourceId}
    `;
    expect(row).toBeDefined();
    expect(Number(row.created_by_run_id)).toBe(run.id);
    expect(row.created_by_actor_type).toBe(DEFAULT_ACTOR.actorType);
    expect(row.created_by_actor_id).toBe(DEFAULT_ACTOR.actorId);
  });

  it("run-less re-add (direct API path) preserves an existing run attribution", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "agent_chat", title: "note-preserve", input: {} });
    const args = { title: "Traced Note", text: "Content written first by a chat run." };

    const { sourceId } = await addMemoryNote(db, { ...args, runId: run.id });
    // Re-add identical text through the run-less path (no runId).
    await addMemoryNote(db, args);

    const [row] = await db<{ created_by_run_id: string | null }[]>`
      SELECT created_by_run_id FROM source_records WHERE source_id = ${sourceId}
    `;
    expect(Number(row.created_by_run_id)).toBe(run.id);
  });

  it("idempotent: adding identical note twice does not duplicate memory_chunks", async () => {
    const db = getDb();
    const args = { title: "Idempotent Note", text: "Unique sourcing content for dedup test." };

    await addMemoryNote(db, args);
    const [{ n: countAfterFirst }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM memory_chunks
    `;

    await addMemoryNote(db, args);
    const [{ n: countAfterSecond }] = await db<{ n: number }[]>`
      SELECT count(*)::int AS n FROM memory_chunks
    `;

    expect(countAfterSecond).toBe(countAfterFirst);
  });
});

describe("addMemoryNoteTool", () => {
  it("has name 'add_memory_note' and permissionClass 'write_internal'", () => {
    expect(addMemoryNoteTool.name).toBe("add_memory_note");
    expect(addMemoryNoteTool.permissionClass).toBe("write_internal");
  });

  it("argsSchema rejects empty title", () => {
    const result = addMemoryNoteTool.argsSchema.safeParse({ title: "", text: "hello" });
    expect(result.success).toBe(false);
  });

  it("argsSchema rejects empty text", () => {
    const result = addMemoryNoteTool.argsSchema.safeParse({ title: "My Note", text: "" });
    expect(result.success).toBe(false);
  });

  it("argsSchema accepts valid title and text", () => {
    const result = addMemoryNoteTool.argsSchema.safeParse({
      title: "My Note",
      text: "Some content.",
    });
    expect(result.success).toBe(true);
  });
});
