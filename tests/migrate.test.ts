import { mkdtemp, mkdir, rm, writeFile } from "fs/promises";
import { tmpdir } from "os";
import { join } from "path";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

describe("runMigrations()", () => {
  let tempRoot: string | null = null;
  let cwdSpy: ReturnType<typeof vi.spyOn> | null = null;

  afterEach(async () => {
    cwdSpy?.mockRestore();
    cwdSpy = null;
    if (tempRoot) {
      await rm(tempRoot, { recursive: true, force: true });
      tempRoot = null;
    }
    await closeDb();
  });

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
  });

  it("records applied migration files with the schema change", async () => {
    tempRoot = await mkdtemp(join(tmpdir(), "sourcecado-migrations-"));
    const migrationsDir = join(tempRoot, "src", "migrations");
    await mkdir(migrationsDir, { recursive: true });
    await writeFile(
      join(migrationsDir, "001_create_probe.sql"),
      "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY);",
    );
    cwdSpy = vi.spyOn(process, "cwd").mockReturnValue(tempRoot);

    const db = getDb();
    await db`DROP TABLE IF EXISTS migration_probe CASCADE`;
    await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;

    await runMigrations(db);

    const migrationRows = await db`
      SELECT name FROM schema_migrations WHERE name = '001_create_probe.sql'
    `;
    const tableRows = await db`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'migration_probe'
    `;
    expect(migrationRows.length).toBe(1);
    expect(tableRows.length).toBe(1);
  });
});
