import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

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

describe("002 memory schema migration", () => {
  beforeEach(async () => {
    await resetMemoryTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("creates all 5 memory tables", async () => {
    const db = getDb();
    const result = await db<{ table_name: string }[]>`
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN (
          'source_records',
          'memory_chunks',
          'semantic_facts',
          'extraction_runs',
          'source_permissions'
        )
      ORDER BY table_name
    `;
    const names = result.map((r) => r.table_name).sort();
    expect(names).toEqual([
      "extraction_runs",
      "memory_chunks",
      "semantic_facts",
      "source_permissions",
      "source_records",
    ]);
  });

  it("memory_chunks.embedding has udt_name = vector", async () => {
    const db = getDb();
    const result = await db<{ udt_name: string }[]>`
      SELECT udt_name
      FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name = 'memory_chunks'
        AND column_name = 'embedding'
    `;
    expect(result).toHaveLength(1);
    expect(result[0].udt_name).toBe("vector");
  });

  it("memory_chunks_embedding_idx hnsw index exists", async () => {
    const db = getDb();
    const result = await db<{ indexname: string }[]>`
      SELECT indexname
      FROM pg_indexes
      WHERE schemaname = 'public'
        AND tablename = 'memory_chunks'
        AND indexname = 'memory_chunks_embedding_idx'
    `;
    expect(result).toHaveLength(1);
    expect(result[0].indexname).toBe("memory_chunks_embedding_idx");
  });

  it("memory_chunks has unique constraint on (source_record_id, chunk_index)", async () => {
    const db = getDb();
    const result = await db<{ constraint_name: string }[]>`
      SELECT tc.constraint_name
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON kcu.constraint_name = tc.constraint_name
        AND kcu.table_schema = tc.table_schema
        AND kcu.table_name = tc.table_name
      WHERE tc.table_schema = 'public'
        AND tc.table_name = 'memory_chunks'
        AND tc.constraint_type = 'UNIQUE'
        AND kcu.column_name IN ('source_record_id', 'chunk_index')
      GROUP BY tc.constraint_name
      HAVING count(*) = 2
    `;
    expect(result.length).toBeGreaterThanOrEqual(1);
  });

  it("source_permissions has unique constraint on (principal_type, principal_id, source_id)", async () => {
    const db = getDb();
    const result = await db<{ constraint_name: string }[]>`
      SELECT tc.constraint_name
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON kcu.constraint_name = tc.constraint_name
        AND kcu.table_schema = tc.table_schema
        AND kcu.table_name = tc.table_name
      WHERE tc.table_schema = 'public'
        AND tc.table_name = 'source_permissions'
        AND tc.constraint_type = 'UNIQUE'
        AND kcu.column_name IN ('principal_type', 'principal_id', 'source_id')
      GROUP BY tc.constraint_name
      HAVING count(*) = 3
    `;
    expect(result.length).toBeGreaterThanOrEqual(1);
  });
});
