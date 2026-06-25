import { closeDb, getDb } from "../src/lib/db.js";
import { refreshMemory } from "../src/lib/memory/extract.js";

async function main(): Promise<void> {
  const db = getDb();
  try {
    const result = await refreshMemory(db);
    process.stdout.write(
      `Refresh complete: ${result.chunksProcessed} chunk(s) processed, ` +
        `${result.extracted} extracted, ${result.reused} reused, ${result.failed} failed.\n`
    );
  } finally {
    await closeDb();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
