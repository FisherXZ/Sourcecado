import { closeDb, getDb } from "../src/lib/db.js";
import { ingestFolder } from "../src/lib/memory/ingest.js";

async function main(): Promise<void> {
  const folderPath = process.argv[2];
  if (!folderPath) {
    process.stderr.write("Usage: tsx scripts/ingest.ts <dir>\n");
    process.exit(1);
  }

  const db = getDb();
  try {
    const result = await ingestFolder(db, folderPath);
    const summary = `Ingested ${result.processed} source file${result.processed === 1 ? "" : "s"}; skipped ${result.skipped}.`;
    process.stdout.write(summary + "\n");

    if (result.skippedFiles.length > 0) {
      process.stdout.write("\nSkipped files:\n");
      for (const skipped of result.skippedFiles) {
        process.stdout.write(`  - ${skipped.path} [${skipped.category}]: ${skipped.reason}\n`);
      }
    }
  } finally {
    await closeDb();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
