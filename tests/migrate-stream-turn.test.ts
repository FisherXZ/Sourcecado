import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("004_model_calls_stream_turn migration", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("allows call_kind='stream_turn'", async () => {
    const db = getDb();
    await db`
      INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
      VALUES ('t', '1', 'h', 'anthropic', 'claude-sonnet-4-6', 'stream_turn', 'running')
    `;
    const rows = await db`SELECT call_kind FROM model_calls`;
    expect(rows).toHaveLength(1);
    expect(rows[0]?.call_kind).toBe("stream_turn");
  });

  it("still allows the four original call_kind values", async () => {
    const db = getDb();
    for (const kind of ["generate_text", "generate_object", "embed", "embed_many"]) {
      await db`
        INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
        VALUES ('t', '1', 'h', 'anthropic', 'm', ${kind}, 'running')
      `;
    }
    const rows = await db`SELECT count(*) FROM model_calls`;
    expect(Number(rows[0]?.count)).toBe(4);
  });

  it("still rejects an invalid call_kind", async () => {
    const db = getDb();
    await expect(
      db`
        INSERT INTO model_calls (task_name, prompt_version, prompt_hash, provider, model, call_kind, status)
        VALUES ('t', '1', 'h', 'anthropic', 'm', 'bogus', 'running')
      `,
    ).rejects.toThrow();
  });
});
