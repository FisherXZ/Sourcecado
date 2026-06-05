#!/usr/bin/env node
import { fileURLToPath } from "node:url";
import { buildSourcingMemoryAnswer } from "./answer.js";
import { DEFAULT_DATABASE_PATH, openMemoryDatabase } from "./db.js";
import { ingestFolder } from "./ingest.js";
import { loadProcedures } from "./procedures.js";
import { refreshMemory } from "./refresh.js";

function help(): string {
  return [
    "Usage:",
    "  sourcyavo ask \"Who needs follow-up?\"",
    "  sourcyavo ingest <dir>",
    "  sourcyavo refresh"
  ].join("\n");
}

export async function main(argv = process.argv.slice(2)): Promise<number> {
  const [command, ...args] = argv;

  if (!command || command === "--help" || command === "-h") {
    console.log(help());
    return 0;
  }

  if (command === "ask") {
    const question = args.join(" ").trim();
    if (!question) {
      console.error("Usage: sourcyavo ask \"Who needs follow-up?\"");
      return 1;
    }

    const db = openMemoryDatabase(DEFAULT_DATABASE_PATH);
    loadProcedures();
    try {
      console.log(buildSourcingMemoryAnswer(db, question));
      return 0;
    } finally {
      db.close();
    }
  }

  if (command === "ingest") {
    const folder = args[0];
    if (!folder) {
      console.error("Usage: sourcyavo ingest <dir>");
      return 1;
    }
    const db = openMemoryDatabase(DEFAULT_DATABASE_PATH);
    try {
      const result = ingestFolder(db, folder);
      console.log(
        `Ingested ${result.processed} source file${result.processed === 1 ? "" : "s"}; skipped ${result.skipped}.`
      );
      return result.processed > 0 || result.skipped >= 0 ? 0 : 1;
    } catch (error) {
      console.error(`Ingest failed: ${errorMessage(error)}`);
      return 1;
    } finally {
      db.close();
    }
  }

  if (command === "refresh") {
    const db = openMemoryDatabase(DEFAULT_DATABASE_PATH);
    try {
      const result = await refreshMemory(db);
      console.log(
        `Refresh complete: ${result.extracted} extracted, ${result.reused} reused, ${result.failed} failed across ${result.chunksProcessed} chunks.`
      );
      return result.failed > 0 ? 1 : 0;
    } catch (error) {
      console.error(`Refresh failed: ${errorMessage(error)}`);
      return 1;
    } finally {
      db.close();
    }
  }

  console.error(`Unknown command: ${command}`);
  console.error(help());
  return 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().then((exitCode) => {
    process.exitCode = exitCode;
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
