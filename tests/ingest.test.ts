import { existsSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { ingestFolder } from "../src/ingest.js";

const tempDirs: string[] = [];

function tempDir(prefix = "sourcyavo-ingest-test-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function tempDb(dir: string): MemoryDatabase {
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

function getRows<T>(db: MemoryDatabase, table: string): T[] {
  return db.prepare(`select * from ${table} order by id`).all() as T[];
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("ingestFolder", () => {
  it("ingests supported files with metadata, hashes, chunks, embeddings, and citations", () => {
    const dir = tempDir();
    writeFileSync(
      join(dir, "spring.md"),
      [
        "---",
        "title: Spring 2026 Sourcing",
        "source_type: markdown",
        "---",
        "Jane Doe met the AI safety group.",
        "She needs follow-up after finals."
      ].join("\n")
    );
    writeFileSync(join(dir, "notes.txt"), "Plain text sourcing note for Alex.");
    writeFileSync(join(dir, "tracker.csv"), "name,status\nJane,contacted\n");
    writeFileSync(join(dir, "thread.eml"), "Subject: Intro\n\nRaw email body about Maya.");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{
      path: string;
      title: string;
      source_type: string;
      content_hash: string;
      raw_text: string;
    }>(db, "source_records");
    const chunks = getRows<{
      source_record_id: number;
      chunk_index: number;
      text: string;
      chunk_hash: string;
      embedding: string;
      citation: string;
    }>(db, "memory_chunks");
    const errors = getRows<{ path: string; reason: string }>(db, "ingest_errors");

    expect(result).toEqual({ processed: 4, skipped: 0 });
    expect(sources).toHaveLength(4);
    expect(sources.map((source) => source.source_type).sort()).toEqual([
      "csv",
      "email",
      "markdown",
      "text"
    ]);
    expect(sources.find((source) => basename(source.path) === "spring.md")).toMatchObject({
      title: "Spring 2026 Sourcing",
      source_type: "markdown",
      raw_text: "Jane Doe met the AI safety group.\nShe needs follow-up after finals."
    });
    expect(sources.every((source) => /^[a-f0-9]{64}$/.test(source.content_hash))).toBe(true);
    expect(chunks.length).toBeGreaterThanOrEqual(4);
    expect(chunks.every((chunk) => /^[a-f0-9]{64}$/.test(chunk.chunk_hash))).toBe(true);
    expect(chunks.every((chunk) => Array.isArray(JSON.parse(chunk.embedding)))).toBe(true);
    expect(chunks.every((chunk) => chunk.citation.includes("#chunk-"))).toBe(true);
    expect(errors).toEqual([]);

    db.close();
  });

  it("logs unsupported files and continues ingesting supported neighbors", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "ok.txt"), "Supported note.");
    writeFileSync(join(dir, "image.png"), "not really an image");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string }>(db, "source_records");
    const chunks = getRows<{ text: string }>(db, "memory_chunks");
    const errors = getRows<{ path: string; reason: string }>(db, "ingest_errors");

    expect(result).toEqual({ processed: 1, skipped: 1 });
    expect(sources).toHaveLength(1);
    expect(chunks).toHaveLength(1);
    expect(basename(errors[0].path)).toBe("image.png");
    expect(errors[0].reason).toContain("Unsupported file extension");

    db.close();
  });

  it("logs empty files without creating source or chunk rows", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "empty.md"), "  \n\t");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    expect(result).toEqual({ processed: 0, skipped: 1 });
    expect(getRows(db, "source_records")).toEqual([]);
    expect(getRows(db, "memory_chunks")).toEqual([]);
    expect(getRows<{ path: string; reason: string }>(db, "ingest_errors")).toMatchObject([
      { reason: "File is empty after parsing" }
    ]);

    db.close();
  });

  it("logs unreadable or malformed file entries without aborting the folder", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "after.txt"), "This should still be indexed.");
    const brokenTarget = join(dir, "missing.txt");
    const brokenLink = join(dir, "broken.txt");
    if (!existsSync(brokenTarget)) {
      symlinkSync(brokenTarget, brokenLink);
    }

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string; title: string }>(db, "source_records");
    const errors = getRows<{ path: string; reason: string }>(db, "ingest_errors");

    expect(result).toEqual({ processed: 1, skipped: 1 });
    expect(sources).toHaveLength(1);
    expect(sources[0].title).toBe("after");
    expect(basename(errors[0].path)).toBe("broken.txt");
    expect(errors[0].reason).toContain("Failed to read file");

    db.close();
  });
});
