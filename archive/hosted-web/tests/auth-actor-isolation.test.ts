import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { MemoryActor } from "@/lib/memory/actor";
import { ingestFiles } from "@/lib/memory/ingest";
import { searchMemory } from "@/lib/memory/retrieve";
import { listSources } from "@/lib/memory/sources";
import { buildMemoryAnswerInstructions } from "@/lib/context";

// H1's load-bearing property: once real users replace the shared
// DEFAULT_ACTOR sentinel, one director's memory must not be reachable by
// another. The permission machinery predates H1 — what is new is that
// actorType "user" (rather than "test_client") flows through it, which the
// source_permissions principal_type CHECK has to accept.
const DIRECTOR_A: MemoryActor = { actorType: "user", actorId: "1" };
const DIRECTOR_B: MemoryActor = { actorType: "user", actorId: "2" };

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

describe("per-user memory isolation", () => {
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

  it("grants the ingesting director read on what they import, and no one else", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") }], DIRECTOR_A);

    await expect(listSources(db, DIRECTOR_A)).resolves.toHaveLength(1);
    await expect(listSources(db, DIRECTOR_B)).resolves.toEqual([]);
  });

  it("excludes another director's chunks from retrieval", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") }], DIRECTOR_A);

    const mine = await searchMemory(db, { query: "Acme sourcing target", actor: DIRECTOR_A });
    const theirs = await searchMemory(db, { query: "Acme sourcing target", actor: DIRECTOR_B });

    expect(mine.chunks.length).toBeGreaterThan(0);
    expect(theirs.chunks).toEqual([]);
  });

  // The system prompt embeds a memory index of source titles. Scoping only
  // search_memory would still leak those titles through the prompt itself.
  it("keeps another director's source titles out of the system prompt", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") }], DIRECTOR_A);

    const promptA = await buildMemoryAnswerInstructions(db, DIRECTOR_A);
    const promptB = await buildMemoryAnswerInstructions(db, DIRECTOR_B);

    expect(promptA).toContain("acme");
    expect(promptB).not.toContain("acme");
  });

  it("keeps two directors' imports separate", async () => {
    const db = getDb();
    await ingestFiles(db, [{ name: "acme.md", bytes: bytes("# Acme\n\nAcme is a sourcing target.") }], DIRECTOR_A);
    await ingestFiles(db, [{ name: "beta.md", bytes: bytes("# Beta\n\nBeta is a sourcing target.") }], DIRECTOR_B);

    const a = await listSources(db, DIRECTOR_A);
    const b = await listSources(db, DIRECTOR_B);

    expect(a.map((s) => s.title)).toEqual(["acme"]);
    expect(b.map((s) => s.title)).toEqual(["beta"]);
  });
});
