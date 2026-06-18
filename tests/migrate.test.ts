import { getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

describe("runMigrations()", () => {
  it("creates the schema_migrations table on a fresh database", async () => {
    const db = getDb();
    // wipe any prior run so this test is repeatable
    await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;

    await runMigrations(db);

    const rows = await db`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'schema_migrations'
    `;
    expect(rows.length).toBe(1);
    await db.end();
  });
});
