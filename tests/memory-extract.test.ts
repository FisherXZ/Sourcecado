import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import {
  markConflictsAndStaleFacts,
  normalizeMemoryKey,
  refreshMemory,
} from "@/lib/memory/extract";
import { createMockExtractor } from "@/extractors/mock";
import type { ExtractionInput } from "@/extractors/types";
import type { ExtractedCandidate } from "@/types";

type Db = ReturnType<typeof getDb>;

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

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

async function insertSource(
  db: Db,
  opts: { sourceId: string; path: string; contentHash?: string; sourceType?: string }
): Promise<string> {
  const [row] = await db<{ id: string }[]>`
    INSERT INTO source_records (source_id, path, source_type, content_hash)
    VALUES (
      ${opts.sourceId},
      ${opts.path},
      ${opts.sourceType ?? "markdown"},
      ${opts.contentHash ?? "hash-" + opts.sourceId}
    )
    RETURNING id
  `;
  return row.id;
}

async function insertChunk(
  db: Db,
  opts: { sourceRecordId: string; text: string; chunkHash: string; citation: string; chunkIndex?: number }
): Promise<string> {
  const [row] = await db<{ id: string }[]>`
    INSERT INTO memory_chunks (source_record_id, chunk_index, text, chunk_hash, citation)
    VALUES (
      ${opts.sourceRecordId},
      ${opts.chunkIndex ?? 0},
      ${opts.text},
      ${opts.chunkHash},
      ${opts.citation}
    )
    RETURNING id
  `;
  return row.id;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("refreshMemory (postgres)", () => {
  beforeEach(async () => {
    await resetMemoryTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  // -------------------------------------------------------------------------
  // accept vs candidate threshold
  // -------------------------------------------------------------------------

  it("inserts 'accepted' for confidence >= 0.75, 'candidate' for < 0.75", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-threshold", path: "/threshold.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Threshold test.",
      chunkHash: "ch-threshold",
      citation: "src-threshold#chunk-1",
    });

    const candidates: ExtractedCandidate[] = [
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.8, evidenceText: "e1" },
      { kind: "semantic_fact", subject: "Bob", predicate: "role", object: "Engineer", confidence: 0.7, evidenceText: "e2" },
    ];
    const result = await refreshMemory(db, { selectExtractor: () => createMockExtractor(candidates) });

    expect(result.chunksProcessed).toBe(1);
    expect(result.extracted).toBe(1);

    const facts = await db<{ subject: string; status: string }[]>`
      SELECT subject, status FROM semantic_facts ORDER BY subject
    `;
    expect(facts).toHaveLength(2);
    expect(facts.find((f) => f.subject === "Alice")?.status).toBe("accepted");
    expect(facts.find((f) => f.subject === "Bob")?.status).toBe("candidate");
  });

  it("treats confidence exactly at 0.75 as 'accepted'", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-exact", path: "/exact.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Boundary test.",
      chunkHash: "ch-exact",
      citation: "src-exact#chunk-1",
    });

    const candidates: ExtractedCandidate[] = [
      { kind: "semantic_fact", subject: "Carol", predicate: "skill", object: "TypeScript", confidence: 0.75, evidenceText: "e" },
    ];
    await refreshMemory(db, { selectExtractor: () => createMockExtractor(candidates) });

    const [fact] = await db<{ status: string }[]>`SELECT status FROM semantic_facts WHERE subject = 'Carol'`;
    expect(fact.status).toBe("accepted");
  });

  // -------------------------------------------------------------------------
  // conflict detection
  // -------------------------------------------------------------------------

  it("marks both facts 'conflicted' when same subject+predicate has different objects", async () => {
    const db = getDb();
    const src1 = await insertSource(db, { sourceId: "src-c1", path: "/c1.md", contentHash: "h1" });
    const src2 = await insertSource(db, { sourceId: "src-c2", path: "/c2.md", contentHash: "h2" });
    const chunk1Id = await insertChunk(db, {
      sourceRecordId: src1,
      text: "Alice at Acme.",
      chunkHash: "ch-c1",
      citation: "src-c1#chunk-1",
    });
    await insertChunk(db, {
      sourceRecordId: src2,
      text: "Alice at BetaCorp.",
      chunkHash: "ch-c2",
      citation: "src-c2#chunk-1",
    });

    const ext1 = createMockExtractor([
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.9, evidenceText: "e1" },
    ]);
    const ext2 = createMockExtractor([
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "BetaCorp", confidence: 0.85, evidenceText: "e2" },
    ]);

    await refreshMemory(db, {
      selectExtractor: (input: ExtractionInput) =>
        input.sourcePath.includes("c1.md") ? ext1 : ext2,
    });

    const facts = await db<{ object: string; status: string }[]>`
      SELECT object, status FROM semantic_facts
      WHERE subject = 'Alice' AND predicate = 'works_at'
      ORDER BY object
    `;
    expect(facts).toHaveLength(2);
    expect(facts.every((f) => f.status === "conflicted")).toBe(true);
  });

  // -------------------------------------------------------------------------
  // stale — vanished accepted fact
  // -------------------------------------------------------------------------

  it("restores a vanished accepted fact as 'stale' on the second refresh", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-stale", path: "/stale.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Alice at Acme.",
      chunkHash: "ch-stale",
      citation: "src-stale#chunk-1",
    });

    const ext1 = createMockExtractor([
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.9, evidenceText: "e1" },
    ]);
    await refreshMemory(db, { selectExtractor: () => ext1 });

    const [firstFact] = await db<{ status: string }[]>`SELECT status FROM semantic_facts WHERE subject = 'Alice'`;
    expect(firstFact.status).toBe("accepted");

    // Clear extraction cache so the second run re-extracts (doesn't reuse ext1's cached candidates).
    await db`DELETE FROM extraction_runs`;

    // Second refresh: only Bob is extracted — Alice vanishes.
    const ext2 = createMockExtractor([
      { kind: "semantic_fact", subject: "Bob", predicate: "role", object: "CTO", confidence: 0.9, evidenceText: "e2" },
    ]);
    await refreshMemory(db, { selectExtractor: () => ext2 });

    const [staleFact] = await db<{ status: string }[]>`SELECT status FROM semantic_facts WHERE subject = 'Alice'`;
    expect(staleFact.status).toBe("stale");

    const [bobFact] = await db<{ status: string }[]>`SELECT status FROM semantic_facts WHERE subject = 'Bob'`;
    expect(bobFact.status).toBe("accepted");
  });

  // -------------------------------------------------------------------------
  // stale — orphaned source_chunk_id
  // -------------------------------------------------------------------------

  it("marks a fact 'stale' when its source_chunk_id points to a missing chunk", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-orphan", path: "/orphan.md" });

    // Temporarily drop the FK so we can insert an orphaned source_chunk_id.
    // In production, ON DELETE SET NULL prevents this; this test exercises the
    // defensive markConflictsAndStaleFacts query.
    await db`ALTER TABLE semantic_facts DROP CONSTRAINT IF EXISTS semantic_facts_source_chunk_id_fkey`;
    await db`
      INSERT INTO semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status)
      VALUES ('Charlie', 'role', 'Designer', ${srcId}, 999999, 0.9, 'accepted')
    `;
    await db`
      ALTER TABLE semantic_facts
      ADD CONSTRAINT semantic_facts_source_chunk_id_fkey
      FOREIGN KEY (source_chunk_id) REFERENCES memory_chunks(id) ON DELETE SET NULL NOT VALID
    `;

    await markConflictsAndStaleFacts(db);

    const [fact] = await db<{ status: string }[]>`
      SELECT status FROM semantic_facts WHERE subject = 'Charlie'
    `;
    expect(fact.status).toBe("stale");
  });

  // -------------------------------------------------------------------------
  // cache reuse
  // -------------------------------------------------------------------------

  it("reuses extraction_runs cache on second refresh of unchanged chunks", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-cache", path: "/cache.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Cache test.",
      chunkHash: "ch-cache",
      citation: "src-cache#chunk-1",
    });

    let callCount = 0;
    const countingExtractor = createMockExtractor(async (_input: ExtractionInput) => {
      callCount++;
      return [
        { kind: "semantic_fact" as const, subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.9, evidenceText: "e1" },
      ];
    });

    const result1 = await refreshMemory(db, { selectExtractor: () => countingExtractor });
    expect(result1.extracted).toBe(1);
    expect(result1.reused).toBe(0);
    expect(callCount).toBe(1);

    const result2 = await refreshMemory(db, { selectExtractor: () => countingExtractor });
    expect(result2.extracted).toBe(0);
    expect(result2.reused).toBe(1);
    expect(callCount).toBe(1); // extractor not called again
  });

  // -------------------------------------------------------------------------
  // failed extraction
  // -------------------------------------------------------------------------

  it("continues and increments failed when extractor throws", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-fail", path: "/fail.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Will fail.",
      chunkHash: "ch-fail",
      citation: "src-fail#chunk-1",
    });

    const failingExtractor = {
      type: "mock",
      version: "1",
      async extract(_input: ExtractionInput): Promise<ExtractedCandidate[]> {
        throw new Error("test extraction failure");
      },
    };

    const result = await refreshMemory(db, { selectExtractor: () => failingExtractor });
    expect(result.failed).toBe(1);
    expect(result.extracted).toBe(0);

    const [runRow] = await db<{ status: string; error: string }[]>`
      SELECT status, error FROM extraction_runs
    `;
    expect(runRow.status).toBe("failed");
    expect(runRow.error).toContain("test extraction failure");
  });

  // -------------------------------------------------------------------------
  // entity / relationship candidates are ignored
  // -------------------------------------------------------------------------

  it("skips entity and relationship candidates, only inserts semantic_facts", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-kinds", path: "/kinds.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Mixed kinds.",
      chunkHash: "ch-kinds",
      citation: "src-kinds#chunk-1",
    });

    const candidates: ExtractedCandidate[] = [
      { kind: "entity", subject: "Alice", entityType: "person", confidence: 0.9, evidenceText: "e" },
      { kind: "relationship", subject: "Alice", object: "Acme", relationshipType: "works_at", confidence: 0.9, evidenceText: "e" },
      { kind: "semantic_fact", subject: "Alice", predicate: "email", object: "alice@acme.com", confidence: 0.95, evidenceText: "e" },
    ];

    await refreshMemory(db, { selectExtractor: () => createMockExtractor(candidates) });

    const facts = await db<{ subject: string; predicate: string }[]>`
      SELECT subject, predicate FROM semantic_facts
    `;
    expect(facts).toHaveLength(1);
    expect(facts[0].predicate).toBe("email");
  });

  // -------------------------------------------------------------------------
  // dedup within a single refresh
  // -------------------------------------------------------------------------

  it("deduplicates identical semantic_facts from the same chunk", async () => {
    const db = getDb();
    const srcId = await insertSource(db, { sourceId: "src-dedup", path: "/dedup.md" });
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Dedup test.",
      chunkHash: "ch-dedup",
      citation: "src-dedup#chunk-1",
    });

    const candidates: ExtractedCandidate[] = [
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.9, evidenceText: "e1" },
      { kind: "semantic_fact", subject: "Alice", predicate: "works_at", object: "Acme", confidence: 0.85, evidenceText: "e2" },
    ];

    await refreshMemory(db, { selectExtractor: () => createMockExtractor(candidates) });

    const [{ n }] = await db<{ n: number }[]>`SELECT count(*)::int AS n FROM semantic_facts`;
    expect(n).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// normalizeMemoryKey unit tests
// ---------------------------------------------------------------------------

describe("normalizeMemoryKey", () => {
  it("normalizes strings for dedup key comparison", () => {
    expect(normalizeMemoryKey("  Alice Smith  ")).toBe("alice-smith");
    expect(normalizeMemoryKey("Acme Corp.")).toBe("acme-corp");
    expect(normalizeMemoryKey("AI Safety")).toBe("ai-safety");
    expect(normalizeMemoryKey("'quoted'")).toBe("quoted");
  });
});
