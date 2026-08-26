import { closeDb, getDb } from "../src/lib/db.js";
import { runMigrations } from "../src/lib/migrate.js";

async function main(): Promise<void> {
  const db = getDb();
  try {
    await runMigrations(db);
    process.stdout.write("Migrations applied.\n");
  } finally {
    await closeDb();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
