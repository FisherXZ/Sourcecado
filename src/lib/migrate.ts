import postgres from "postgres";
import { readdir, readFile } from "fs/promises";
import { join } from "path";

export async function runMigrations(db: postgres.Sql): Promise<void> {
  // Create the bookkeeping table if it doesn't exist yet
  await db`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      id        SERIAL PRIMARY KEY,
      name      TEXT NOT NULL UNIQUE,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  // Find migration files in src/migrations/, sorted by name (001_, 002_, ...)
  const migrationsDir = join(process.cwd(), "src", "migrations");
  let files: string[] = [];
  try {
    files = (await readdir(migrationsDir))
      .filter((f) => f.endsWith(".sql"))
      .sort();
  } catch {
    // No migrations directory yet — nothing to run
    return;
  }

  for (const file of files) {
    const already = await db`
      SELECT 1 FROM schema_migrations WHERE name = ${file}
    `;
    if (already.length > 0) continue;

    const sql = await readFile(join(migrationsDir, file), "utf8");
    await db.unsafe(sql);
    await db`INSERT INTO schema_migrations (name) VALUES (${file})`;
  }
}
