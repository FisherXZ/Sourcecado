#!/usr/bin/env node
import { fileURLToPath } from "node:url";
import { DEFAULT_DATABASE_PATH, openMemoryDatabase } from "./db.js";
import { formatIngestReport, ingestFolder } from "./ingest.js";
import { loadProcedures } from "./procedures.js";
import { MemoryReader, resolveAccessContext } from "./read-service.js";
import { refreshMemory } from "./refresh.js";

function help(): string {
  return [
    "Usage:",
    "  sourcyavo ask --client <id> \"Who needs follow-up?\"",
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
    const { client, rest } = parseClientFlag(args);
    const question = rest.join(" ").trim();
    if (!question) {
      console.error("Usage: sourcyavo ask --client <id> \"Who needs follow-up?\"");
      return 1;
    }
    if (!client) {
      console.error("Refusing unscoped read: pass --client <id> to scope the ask.");
      return 1;
    }

    const db = openMemoryDatabase(DEFAULT_DATABASE_PATH);
    loadProcedures();
    try {
      const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: client });
      const reader = new MemoryReader(db, ctx);
      console.log(reader.ask(question));
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
      console.log(formatIngestReport(result, folder));
      return result.skipped > 0 ? 1 : 0;
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

function parseClientFlag(args: string[]): { client: string | null; rest: string[] } {
  const rest: string[] = [];
  let client: string | null = null;

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--client") {
      const next = args[index + 1] ?? null;
      client = next && !next.startsWith("-") ? next : null;
      index += 1;
      continue;
    }
    if (arg.startsWith("--client=")) {
      const value = arg.slice("--client=".length);
      client = value && !value.startsWith("-") ? value : null;
      continue;
    }
    rest.push(arg);
  }

  return { client, rest };
}
